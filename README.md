# kiro-ngx

基于 [kiro.rs](https://github.com/hank9999/kiro.rs) / [kiro.py](https://github.com/fruktoguo/kiro.py) 的增强版 Kiro IDE API 代理，专注**大上下文、低延迟、高稳定性**。

## 相比上游的核心改进

| 特性 | 说明 |
|------|------|
| **1M 上下文窗口** | Claude 4.6 模型自动使用 1,000,000 token 上下文（上游 184K） |
| **主动上下文压缩** | 60% 容量即开始三级渐进压缩，用户无感知：截断旧 tool_result → 截断旧 assistant → 丢弃最旧消息对 |
| **结构安全裁剪** | 所有 history 裁剪都保证 user↔assistant 交替、orphaned tool_result 清理、消息对完整性 |
| **TTFB 优化** | 移除热路径上的远程 token 计数（300s 超时），改用本地估算（~1ms） |
| **HTTP/2 多路复用** | httpx + h2，减少 TCP 连接建立失败率 |
| **连接错误不冷却凭据** | 网络抖动不再导致凭据进入冷却→全面不可用的死循环 |
| **工具名 63 字符限制** | 自动 SHA256 缩短 + 流式响应反向映射，对客户端透明 |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（仅构建前端时需要）

### 安装

```bash
git clone https://github.com/LegnaOS/kiro-ngx.git
cd kiro-ngx
```

### 配置

```bash
cp config.example.json config.json
cp credentials.example.json credentials.json
```

编辑 `config.json`：

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `host` | 监听地址 | `0.0.0.0` |
| `port` | 监听端口 | `8990` |
| `region` | AWS 区域 | `us-east-1` |
| `kiroVersion` | Kiro IDE 版本号 | `0.10.0` |
| `apiKey` | 代理 API Key（客户端调用时使用） | - |
| `adminApiKey` | Admin UI 登录密钥 | - |
| `loadBalancingMode` | 负载均衡：`priority` / `balanced` | `priority` |

编辑 `credentials.json`，填入凭据数组（参考 `credentials.example.json`）。

### 启动

#### Linux / macOS（推荐）

```bash
chmod +x deploy.sh
./deploy.sh          # 自动创建 venv、安装依赖、启动服务
./deploy.sh --pull   # 同时拉取最新代码
PORT=8991 ./deploy.sh
```

#### 手动启动

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

#### Windows

```bash
restart.bat
```

### 使用

- **API 代理**：客户端 API 地址指向 `http://your-host:port`，使用 `config.json` 中的 `apiKey`
- **Admin UI**：浏览器访问 `http://your-host:port/admin`，使用 `adminApiKey` 登录

## 项目结构

```
├── main.py                 # 入口
├── config.py               # 配置加载
├── token_counter.py        # Token 计数（本地估算 + 远程 API）
├── http_client.py          # httpx 客户端（HTTP/2 + transport 重试）
├── deploy.sh / restart.bat # 一键部署
├── anthropic_api/          # Anthropic API 兼容层
│   ├── converter.py        # Kiro ↔ Anthropic 请求转换
│   ├── handlers.py         # 核心处理（压缩、裁剪、auto-continue）
│   ├── stream.py           # 流式响应 + 工具名反向映射
│   └── types.py            # 类型定义
├── kiro/                   # Kiro 核心
│   ├── token_manager.py    # 多凭据管理与轮换
│   ├── provider.py         # API 调用（连接错误隔离）
│   └── parser/             # SSE 事件流解析
├── admin/                  # Admin API + Web UI
├── admin-ui/               # 前端（React + Vite + TailwindCSS）
├── plugins/                # 插件
└── tests/                  # 测试
```

## 致谢

- [hank9999/kiro.rs](https://github.com/hank9999/kiro.rs) — 原始 Rust 实现
- [fruktoguo/kiro.py](https://github.com/fruktoguo/kiro.py) — Python 移植

## License

MIT
