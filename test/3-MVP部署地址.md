# MVP 系统上线部署地址

> 2026-11-06 ｜ 与 `部署说明.docx` 同源

## 1. 访问地址

| 环境 | 地址 | 状态 |
| --- | --- | --- |
| 本机（已验证） | http://127.0.0.1:8000/chat | 当前运行环境，健康检查 /healthz 返回 ok |
| 本机管理后台 | http://127.0.0.1:8000/admin | 审核方/管理员登录后可见 |
| 局域网（同一网段可访问） | http://<本机内网IP>:8000/chat | 启动时加 --host 0.0.0.0 |
| 公网 | 待部署（见第 4 节方案） | 本机无公网出口，尚未发布 |

## 2. 启动步骤

1. `python -m pip install -e ".[web]"`
2. expo-trace web --host 0.0.0.0 --port 8000
3. 浏览器打开 http://127.0.0.1:8000/chat
4. 账号 admin / reviewer / agent，初始密码 demo-1234（EXPO_SEED_PASSWORD 可覆盖）

## 3. 上线自检

| 检查 | 接口 | 预期 |
| --- | --- | --- |
| 探活 | GET /healthz | {"status":"ok"} |
| 状态 | GET /api/v1/health | run_dir、paused、provider |
| 登录 | POST /api/v1/auth/login | 200 + token |
| 问答 | POST /api/v1/chat | SSE，末事件 done |

## 4. 公网发布方案（未执行）

| 方案 | 做法 | 注意 |
| --- | --- | --- |
| 云主机部署 | 把仓库拷到云主机，装 .[web] 依赖，反代 80/443 | 需域名与备案（境内） |
| 内网穿透 | 本机起 8000，用 frp / ngrok 映射出公网地址 | 仅演示，注意演示密码 |
| 容器化 | 写 Dockerfile + compose，一条命令起服务与卷 | 便于交付与复现 |

说明：本机没有公网出口与域名，因此未发布公网地址；课程演示使用本机与局域网地址即可。
