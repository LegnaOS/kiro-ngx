# kiro.py 项目规范

## 项目定位
kiro.py 是 kiro.rs 的 Python 复刻版。两者功能完全一致，kiro.rs 是唯一的参考实现。

## 参考源码位置
- Rust 原版: `../kiro.rs/src/`
- Python 版: 当前目录

## 模块对应关系
| Rust (kiro.rs/src/)              | Python (kiro.py/)                    |
|----------------------------------|--------------------------------------|
| main.rs                         | main.py                              |
| anthropic/converter.rs          | anthropic_api/converter.py           |
| anthropic/handlers.rs           | anthropic_api/handlers.py            |
| anthropic/middleware.rs         | anthropic_api/middleware.py          |
| anthropic/router.rs            | anthropic_api/router.py              |
| anthropic/stream.rs            | anthropic_api/stream.py              |
| anthropic/types.rs             | anthropic_api/types.py               |
| anthropic/websearch.rs         | anthropic_api/websearch.py           |
| kiro/provider.rs               | kiro/provider.py                     |
| kiro/token_manager.rs          | kiro/token_manager.py                |
| kiro/machine_id.rs             | kiro/machine_id.py                   |
| kiro/model/                    | kiro/model/                          |
| kiro/parser/                   | kiro/parser/                         |
| admin/                         | admin/                               |
| admin_ui/                      | admin_ui/                            |
| token.rs                       | token_counter.py (避免 stdlib 冲突)  |
| http_client.rs                 | http_client.py                       |
| common/                        | common/                              |

## 核心原则
1. **遇到 bug 先对照 Rust 原版**：请求体结构、字段名(camelCase)、序列化逻辑、API URL 拼接、请求头构建——全部以 kiro.rs 为准
2. **JSON 字段名一律 camelCase**：与 Rust 的 serde(rename_all = "camelCase") 保持一致
3. **不要猜测上游 API 格式**：直接读 Rust 源码中的 struct 定义和 serde 注解

## 技术栈
- FastAPI + uvicorn (替代 Axum)
- httpx (替代 reqwest)
- asyncio (替代 tokio)
- Python 3.10+

## 关键注意事项
- `token_counter.py` 而非 `token.py`，避免与 Python stdlib `token` 模块冲突
- admin-ui 前端源码在 `admin-ui/src/`，构建产物在 `admin-ui/dist/`（已纳入 git）
- 跨平台重启: Windows 用 subprocess.Popen + os._exit，Linux 用 os.execv
- credentials.json 支持单凭据(dict)和多凭据(list)两种格式

## 调试流程
1. 出现上游 400/格式错误 → 对比 Rust 版的请求体序列化
2. 出现字段缺失 → 检查 to_dict() 的 camelCase 映射
3. 出现鉴权失败 → 对比 Rust 版的 headers 构建
4. 出现流式解析错误 → 对比 Rust 版的 EventStream parser
