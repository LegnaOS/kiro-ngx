# Kiro.py

Kiro.rs 的 Python 复刻版，提供 Kiro IDE 凭据管理和 API 代理服务。

## 功能

- Anthropic API 兼容代理（支持流式/非流式）
- 多凭据管理与自动轮换
- 负载均衡（优先级模式 / 均衡模式）
- Token 自动刷新与过期管理
- Admin Web UI（凭据管理、余额查询、批量导入、系统监控）
- 一键远程更新部署

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（构建前端）
- Git

### 安装

```bash
git clone https://github.com/fruktoguo/kiro.py.git
cd kiro.py
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
| `loadBalancingMode` | 负载均衡模式：`priority` / `balanced` | `priority` |

编辑 `credentials.json`，填入凭据数组：

```json
[
  {
    "refreshToken": "your-refresh-token",
    "authMethod": "social",
    "clientId": "oidc-kiro",
    "clientSecret": "your-client-secret",
    "priority": 0,
    "authRegion": "us-east-1",
    "apiRegion": "us-east-1"
  }
]
```

### 启动

#### Linux（推荐）

```bash
chmod +x deploy.sh
./deploy.sh
```

`deploy.sh` 会自动创建 venv、安装依赖、构建前端、启动服务。

#### 手动启动

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd admin-ui && npm install && npm run build && cd ..

python main.py
```

#### Windows

```bash
pip install -r requirements.txt
cd admin-ui && npm install && npm run build && cd ..
python main.py
```

或使用 `restart.bat` 一键重启。

## 使用

### API 代理

启动后，将客户端的 API 地址指向 `http://your-host:8990`，使用 `config.json` 中的 `apiKey` 作为认证密钥。

### Admin UI

浏览器访问 `http://your-host:8990/admin`，使用 `adminApiKey` 登录。

功能：
- 凭据列表：查看所有凭据状态、余额、调用次数
- 批量导入：支持 JSON 格式批量导入，可选跳过验活
- 编辑凭据文件：直接编辑 `credentials.json`
- 负载均衡切换：优先级模式 / 均衡模式
- 系统监控：CPU 和内存占用
- 一键更新：从 GitHub 拉取最新代码并自动重新部署

### 远程更新

Admin UI 顶部栏会自动检查新版本。有更新时会显示版本号提示，点击即可一键更新（git pull + 构建 + 重启）。

也可以手动更新：

```bash
cd kiro.py
./deploy.sh
```

## 项目结构

```
kiro.py/
├── main.py                 # 入口
├── config.py               # 配置加载
├── VERSION                 # 版本号
├── admin/                  # Admin API
│   ├── handlers.py         # 请求处理器
│   ├── router.py           # 路由注册
│   ├── service.py          # 业务逻辑
│   └── ui_router.py        # 前端静态文件服务
├── anthropic_api/          # Anthropic API 兼容层
│   ├── converter.py        # 请求/响应转换
│   ├── handlers.py         # API 处理器
│   ├── stream.py           # 流式响应
│   └── types.py            # 类型定义
├── kiro/                   # Kiro 核心
│   ├── token_manager.py    # 多凭据管理与轮换
│   ├── provider.py         # API 调用
│   ├── machine_id.py       # Machine ID 生成
│   ├── model/              # 数据模型
│   └── parser/             # 事件流解析器
├── admin-ui/               # 前端源码 (React + Vite)
│   └── src/
├── deploy.sh               # Linux 一键部署脚本
└── restart.bat             # Windows 重启脚本
```

## License

MIT
