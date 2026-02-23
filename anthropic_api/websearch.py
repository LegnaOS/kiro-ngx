"""WebSearch 工具处理模块 - 参考 src/anthropic/websearch.rs"""

import json
import logging
import random
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi.responses import StreamingResponse

from .stream import SseEvent
from .types import MessagesRequest, ErrorResponse

logger = logging.getLogger(__name__)


# === MCP 请求/响应类型 ===

@dataclass
class McpArguments:
    query: str

@dataclass
class McpParams:
    name: str
    arguments: McpArguments

@dataclass
class McpRequest:
    id: str
    jsonrpc: str
    method: str
    params: McpParams

    def to_dict(self) -> dict:
        return {
            "id": self.id, "jsonrpc": self.jsonrpc, "method": self.method,
            "params": {"name": self.params.name, "arguments": {"query": self.params.arguments.query}},
        }


@dataclass
class McpContent:
    content_type: str
    text: str

@dataclass
class McpResult:
    content: List[McpContent]
    is_error: bool

@dataclass
class McpError:
    code: Optional[int] = None
    message: Optional[str] = None

@dataclass
class McpResponse:
    error: Optional[McpError]
    id: str
    jsonrpc: str
    result: Optional[McpResult]

    @classmethod
    def from_dict(cls, data: dict) -> "McpResponse":
        error = None
        if data.get("error"):
            e = data["error"]
            error = McpError(code=e.get("code"), message=e.get("message"))
        result = None
        if data.get("result"):
            r = data["result"]
            result = McpResult(
                content=[McpContent(content_type=c.get("type", ""), text=c.get("text", "")) for c in r.get("content", [])],
                is_error=r.get("isError", False),
            )
        return cls(error=error, id=data.get("id", ""), jsonrpc=data.get("jsonrpc", "2.0"), result=result)


@dataclass
class WebSearchResult:
    title: str
    url: str
    snippet: Optional[str] = None

@dataclass
class WebSearchResults:
    results: List[WebSearchResult]
    total_results: Optional[int] = None
    query: Optional[str] = None
    error: Optional[str] = None


# === 工具函数 ===

def _random_alnum(n: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))

def _random_lower_alnum(n: int) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def has_web_search_tool(req: MessagesRequest) -> bool:
    tools = req.tools
    if not tools or len(tools) != 1:
        return False
    return tools[0].get("name") == "web_search"


def extract_search_query(req: MessagesRequest) -> Optional[str]:
    if not req.messages:
        return None
    first = req.messages[0]
    content = first.get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content:
        block = content[0]
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "")
        else:
            return None
    else:
        return None
    prefix = "Perform a web search for the query: "
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text if text else None


def create_mcp_request(query: str):
    ts = int(time.time() * 1000)
    request_id = f"web_search_tooluse_{_random_alnum(22)}_{ts}_{_random_lower_alnum(8)}"
    tool_use_id = f"srvtoolu_{uuid.uuid4().hex[:32]}"
    req = McpRequest(
        id=request_id, jsonrpc="2.0", method="tools/call",
        params=McpParams(name="web_search", arguments=McpArguments(query=query)),
    )
    return tool_use_id, req


def parse_search_results(mcp_response: McpResponse) -> Optional[WebSearchResults]:
    if not mcp_response.result or not mcp_response.result.content:
        return None
    content = mcp_response.result.content[0]
    if content.content_type != "text":
        return None
    try:
        data = json.loads(content.text)
        results = [WebSearchResult(title=r["title"], url=r["url"], snippet=r.get("snippet"))
                   for r in data.get("results", [])]
        return WebSearchResults(results=results, total_results=data.get("totalResults"), query=data.get("query"))
    except (json.JSONDecodeError, KeyError):
        return None


def _generate_search_summary(query: str, results: Optional[WebSearchResults]) -> str:
    summary = f'Here are the search results for "{query}":\n\n'
    if results:
        for i, r in enumerate(results.results, 1):
            summary += f"{i}. **{r.title}**\n"
            if r.snippet:
                s = r.snippet[:200] + "..." if len(r.snippet) > 200 else r.snippet
                summary += f"   {s}\n"
            summary += f"   Source: {r.url}\n\n"
    else:
        summary += "No results found.\n"
    summary += "\nPlease note that these are web search results and may not be fully accurate or up-to-date."
    return summary


def _generate_websearch_events(
    model: str, query: str, tool_use_id: str,
    search_results: Optional[WebSearchResults], input_tokens: int,
) -> List[SseEvent]:
    events: List[SseEvent] = []
    message_id = f"msg_{uuid.uuid4().hex[:24]}"

    events.append(SseEvent("message_start", {
        "type": "message_start", "message": {
            "id": message_id, "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": 0,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        },
    }))
    events.append(SseEvent("content_block_start", {
        "type": "content_block_start", "index": 0,
        "content_block": {"id": tool_use_id, "type": "server_tool_use", "name": "web_search", "input": {}},
    }))
    events.append(SseEvent("content_block_delta", {
        "type": "content_block_delta", "index": 0,
        "delta": {"type": "input_json_delta", "partial_json": json.dumps({"query": query}, ensure_ascii=False)},
    }))
    events.append(SseEvent("content_block_stop", {"type": "content_block_stop", "index": 0}))

    search_content = []
    if search_results:
        search_content = [{"type": "web_search_result", "title": r.title, "url": r.url,
                           "encrypted_content": r.snippet or "", "page_age": None}
                          for r in search_results.results]
    events.append(SseEvent("content_block_start", {
        "type": "content_block_start", "index": 1,
        "content_block": {"type": "web_search_tool_result", "tool_use_id": tool_use_id, "content": search_content},
    }))
    events.append(SseEvent("content_block_stop", {"type": "content_block_stop", "index": 1}))

    events.append(SseEvent("content_block_start", {
        "type": "content_block_start", "index": 2,
        "content_block": {"type": "text", "text": ""},
    }))
    summary = _generate_search_summary(query, search_results)
    chunk_size = 100
    chars = list(summary)
    for ci in range(0, len(chars), chunk_size):
        chunk = "".join(chars[ci:ci + chunk_size])
        events.append(SseEvent("content_block_delta", {
            "type": "content_block_delta", "index": 2,
            "delta": {"type": "text_delta", "text": chunk},
        }))
    events.append(SseEvent("content_block_stop", {"type": "content_block_stop", "index": 2}))

    output_tokens = (len(summary) + 3) // 4
    events.append(SseEvent("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    events.append(SseEvent("message_stop", {"type": "message_stop"}))
    return events


async def handle_websearch_request(provider, payload: MessagesRequest, input_tokens: int):
    from fastapi.responses import JSONResponse

    query = extract_search_query(payload)
    if not query:
        error = ErrorResponse.new("invalid_request_error", "无法从消息中提取搜索查询")
        return JSONResponse(status_code=400, content=error.to_dict())

    logger.info("处理 WebSearch 请求: query=%s", query)
    tool_use_id, mcp_request = create_mcp_request(query)

    search_results = None
    try:
        resp = await provider.call_mcp(json.dumps(mcp_request.to_dict()))
        body = await resp.aread()
        mcp_resp = McpResponse.from_dict(json.loads(body))
        if mcp_resp.error:
            logger.warning("MCP error: %s", mcp_resp.error.message)
        else:
            search_results = parse_search_results(mcp_resp)
    except Exception as e:
        logger.warning("MCP API 调用失败: %s", e)

    events = _generate_websearch_events(payload.model, query, tool_use_id, search_results, input_tokens)

    async def event_generator():
        for evt in events:
            yield evt.to_sse_string()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
