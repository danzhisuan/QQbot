# QQ 群聊机器人（NapCat + NoneBot2）

个人号 QQ 机器人：**NapCatQQ** 负责登录 QQ 并暴露 OneBot v11 协议，**NoneBot2** 负责事件分发与业务逻辑。业务代码与接入端解耦，将来换 QQ 官方平台或企业微信，只需替换适配器。

## 当前部署形态（已跑通）

两个服务**都跑在阿里云服务器 `203.0.113.10` 的 Docker 里**，通过 Docker 内网直连，公网看不到任何机器人端口。

```
        QQ 服务器
           ▲
           │ NTQQ 协议
┌──────────┴───────────────────────────────────┐
│  阿里云服务器 203.0.113.10                   │
│                                              │
│  ┌────────────────┐      Docker 内网         │
│  │ napcat 容器     │  ws://nonebot:8080/     │
│  │  WebUI :6099    │  onebot/v11/ws          │
│  │  (仅回环暴露)    │ ─────────────────────▶ │
│  └────────────────┘                          │
│                          ┌────────────────┐  │
│                          │ nonebot 容器    │ │
│                          │  src/plugins/*  │ │
│                          └────────┬───────┘  │
│                                   │          │
│  你的浏览器 ◀── SSH 隧道 ◀── 127.0.0.1:6099  │
└──────────────────────────────────────────────┘
                                   │           │
                            LLM API（DeepSeek 等）
```

> **为什么选服务器而不是本地 Windows**：本地 Windows 上 NapCat 撞了三堵墙——
> ① 一键包目录结构与旧文档不符；② DLL 注入必须提权；③ 提权后 Chromium GPU
> 沙箱起不来（`FATAL: GPU process isn't usable`），而 NapCat 启动器不支持
> `--disable-gpu`，形成死循环。Linux + Docker 用 Xvfb 软件渲染，三者皆无。

---

## 1. 目录结构

```
.
├── bot.py                      # 启动入口：注册适配器 + 加载插件
├── pyproject.toml              # [tool.nonebot] 配置（NoneBot 2.5 新格式）
├── requirements.txt            # 依赖唯一来源（本地与容器共用）
├── Dockerfile                  # 非 root 运行 + 国内 pip 源
├── docker-compose.yml          # 编排 napcat + nonebot（含内存上限）
├── .env                        # 运行时配置（600 权限，不入库）
├── deploy/
│   ├── server-setup.sh         # 宿主机 venv + systemd（备用方案，当前未启用）
│   ├── server-prep.sh          # 装 Docker + 加 4GB Swap + 关无用服务
│   ├── docker-mirror.sh        # 配置国内 Docker 镜像源
│   ├── qq-bot.service          # systemd unit（备用）
│   └── tunnel.bat              # 本地 Windows：双击开 SSH 隧道访问 WebUI
├── scripts/
│   ├── smoke_test.py           # 自检：验证适配器与插件能否加载
│   ├── test_tools.py           # 自检：联网搜索（含真实联网测试）
│   └── test_fortune.py         # 自检：今日运势（确定性/分布/格式）
├── data/                       # 全部运行时数据（不入库）
│   ├── ntqq/                   #   QQ 登录态
│   ├── napcat/config/          #   NapCat 与 OneBot 网络配置
│   └── bot/                    #   下面是 NoneBot 侧的持久化数据
│       ├── personas.json       #     人格定义
│       ├── persona_state.json  #     各会话当前人格
│       ├── pylibs/             #     插件商店装的包（重建容器不丢）
│       └── extra_plugins.json  #     要加载的第三方插件清单
└── src/plugins/
    ├── echo/                   # 链路自检：/ping、/echo
    ├── fortune/                # 今日运势：core.py 纯逻辑 + __init__.py 命令层
    │   ├── core.py             #   抽签与渲染（不依赖 NoneBot，可单测）
    │   └── __init__.py         #   /运势 命令、@某人 支持
    ├── webadmin/               # Web 管理台（挂在 FastAPI 上）
    │   ├── page.py             #   前端：单文件 HTML，零外部依赖
    │   ├── store.py            #   插件商店：registry、安装、卸载
    │   └── __init__.py         #   路由、鉴权、日志采集、重启
    └── llm_chat/               # LLM 群聊应答（人格 + 联网 + 发图）
        ├── config.py           #   插件配置
        ├── personas.py         #   人格定义与切换
        ├── tools.py            #   联网搜索工具（Bing / 博查）
        └── __init__.py         #   工具调用循环、发图、命令
```

---

## 2. 日常运维

```bash
cd ~/bot

docker compose ps                  # 查看状态
docker compose logs -f nonebot     # 实时看机器人日志
docker compose logs -f napcat      # 实时看 QQ 端日志
docker compose restart nonebot     # 重启机器人（改了 .env 后执行）
docker compose up -d --build       # 改了代码后重新构建启动
docker stats --no-stream           # 看内存占用
```

**改了 `src/plugins/` 或 `.env` 后**：

```bash
docker compose up -d --build nonebot
```

**开机自启**：`docker` 服务已 `enabled`，两个容器 `restart: unless-stopped`，
服务器重启后会自动拉起。NapCat 靠 `ACCOUNT=1234567890` 快速登录，**不需要重新扫码**。

**访问 NapCat WebUI**（改网络配置、重新登录时用）：

```powershell
# 本地 Windows
deploy\tunnel.bat
# 然后浏览器打开 http://127.0.0.1:6099/webui
# token 获取：ssh myserver "docker logs napcat 2>&1 | grep 'WebUi Token'"
```

---

## 3. 配置说明（`~/bot/.env`）

```ini
HOST=127.0.0.1          # 宿主机运行时的值；容器内由 compose 覆盖为 0.0.0.0:8080
PORT=18080
SUPERUSERS=["100000000"]              # 管理员 QQ，拥有 SUPERUSER 权限
COMMAND_START=["/"]                    # 命令前缀，默认只认 /
ONEBOT_ACCESS_TOKEN=<openssl rand -hex 32>   # 必须与 NapCat 侧完全一致
NAPCAT_UID=1001                        # 宿主机 uid/gid（id -u / id -g）
NAPCAT_GID=1001
NAPCAT_ACCOUNT=1234567890              # 机器人 QQ 号，用于重启后快速登录

LLM_API_BASE=https://api.deepseek.com
LLM_API_KEY=                           # 留空则只不聊天，/ping 等照常
LLM_MODEL=deepseek-flash
```

> `LLM_API_KEY` 是**从大模型平台申请的真实密钥**，不是自己编的密码。
> DeepSeek 当前模型只有 `deepseek-flash` 和 `deepseek-v4-pro`
> （旧的 `deepseek-chat` 已失效）。见 [官方定价页](https://api-docs.deepseek.com/quick_start/pricing)。

---

## 3.5 多个人格与切换

机器人支持多个人格，**按会话独立**（每个群、每个私聊互不影响），切换后重启仍保持。

```
/人格              查看当前人格 + 全部可用人格
/人格 猫娘         切换到「猫娘」
/persona 吐槽役     英文别名同样可用
/reset             清空本会话上下文（不换人格）
```

**权限**：默认仅机器人管理员（`SUPERUSERS`）和本群群主/管理员可切换；
想让群里所有人都能切换，把 `.env` 里的 `LLM_PERSONA_OPEN` 设为 `true`。

**人格定义文件**：`~/bot/data/bot/personas.json`（容器内是 `/app/data/personas.json`）

```json
{
  "猫娘": {
    "description": "可爱猫娘，句尾带「喵」",
    "prompt": "你是生活在 QQ 群里的猫娘，名字叫小咪……",
    "temperature": 0.95,
    "model": "Qwen/Qwen3.8-Flash"
  }
}
```

- 首次启动会自动生成 4 个示例人格：`默认` / `猫娘` / `严谨助手` / `吐槽役`
- **改完即时生效**（带 mtime 热加载），**不用重建镜像、不用重启**
- `temperature` 和 `model` 可选，不写就用全局的
- 切换人格时会**自动清空该会话上下文**，避免人格串味
- `prompt` 里可用 `{model}`（当前模型名）和 `{persona}`（当前人格名）占位符

**关于「你用的什么模型」**：模型本身并不知道自己跑在哪个模型上，所以默认会含糊其辞。
`LLM_REVEAL_MODEL=true`（默认开）会在系统提示词末尾自动附上模型信息，让它能如实回答；
不介意它答不上来的话可以关掉。

### ⚠️ 核心约束：人格只修饰措辞，不影响事实

**这是实测踩坑后加的最重要一条规则。** 「吐槽役」这类"爱开玩笑、阴阳怪气"的人格设定，
会让模型**用外貌描述代替事实回答**——明明认出了后藤一里，却回「说实话我没认出来，
八成是同人画的某粉毛角色」。

所以系统提示词里有一段 `FACT_FIRST_RULE`，明确声明**优先级高于人格设定**：

```
[回答原则] 以下优先级高于你的人格设定：
① 事实优先：知道就如实说，不知道就说不知道。严禁因为「人设」而假装不知道、
   含糊其辞、或用外貌描述代替答案。
② 人格只影响措辞和语气，不影响信息本身。把人格理解成「说话的方式」，
   不是「说不说真话」。
③ 被问到事实性问题（这是谁 / 多少度 / 几点 / 等于几）时，先把准确答案说出来，
   再用你的风格补一句。
```

实测效果（同一批问题 × 全部 4 个人格）：

| 问题 | 猫娘 | 吐槽役 |
|---|---|---|
| **这是谁**（发波奇酱的图） | 这是后藤一里（波奇酱），出自《孤独摇滚！》喵～ | 后藤一里（波奇酱），出自《孤独摇滚！》。…经典社恐脸，没错就是她 |
| **珠峰海拔多少米** | 8848.86 米喵！…小咪爬上去大概会冻成冰棍喵～ | 8848.86 米…就这点高度，也够人类喘半天了 |
| **今天好累啊**（闲聊） | 累了就歇歇嘛，小咪陪着你喵～ | 累就对了，说明你还活着且在被生活摩擦 |

**事实全对，风格全在。** 人格管的是「怎么说」，不是「说什么」。

```bash
# 编辑人格
vim ~/bot/data/bot/personas.json
# 验证 JSON 合法性
python3 -m json.tool ~/bot/data/bot/personas.json
```

当前会话的人格记录在 `~/bot/data/bot/persona_state.json`。

---

## 3.6 联网搜索与发图

**开箱即用，不需要任何 API Key。** 天气走 Open-Meteo，搜索走 Bing：

```
你：上海今天天气怎么样？
  → 模型调用 get_weather → 直出精确数值 → 回答

你：找几张布偶猫的图
  → 模型调用 image_search → 图片直接发到聊天里
```

**用什么工具由模型自己判断**，走的是 **function calling**（已实测该模型支持）。

### 三个工具

| 工具 | 触发场景 | 说明 |
|---|---|---|
| `get_weather` | "上海今天天气""明天要穿外套吗" | [Open-Meteo](https://open-meteo.com/) 免费接口，**直出精确数值**（气温/体感/湿度/风速/降水概率/日出日落），含今明两天 |
| `web_search` | "XX 是什么""最近有什么新闻" | Bing RSS 或博查 API |
| `image_search` | "找几张猫的图""发个壁纸" | 搜图后**直接发图** |

### 为什么天气必须单独做一个工具

**搜索只返回标题+摘要+链接，拿不到页面里的数值。** 实测时用户问天气，
模型搜到了几个天气站，但只能回答"没拿到具体数据，建议你看手机天气 App"——这是真实反馈。

所以天气走独立接口：`城市名 → 地理编码得经纬度 → 取实时+今明预报 → 格式化喂给模型`。
实测效果：

```
用户：我明天去北京要穿外套吗
  [调用] get_weather(city=北京)
  [AI] 明天北京晴，13.6~25.2°C，白天挺舒服，但早晚只有十几度，
       得穿件外套，中午热了脱掉就行。
```

### 搜索的两个后端

| 后端 | 费用 | 说明 |
|---|---|---|
| **bing**（默认） | **免费，无需 Key** | 文字走 `cn.bing.com/search?format=rss`（结构化 XML）；图片走 `cn.bing.com/images` 提取原图直链 |
| bocha | 按量计费，需 Key | 中文搜索质量更好，一次请求同时返回网页+图片 |

配置 `BOCHA_API_KEY` 后 `SEARCH_PROVIDER=auto` 会自动切到博查。

### ⚠️ 搜索的已知局限

**Bing RSS 做的是字面词匹配，不是语义搜索——查询越长越差。**

| 查询 | 结果 |
|---|---|
| `上海天气` | ✅ 全部相关 |
| `DeepSeek V4 发布` | ✅ 全部相关 |
| `今天上海天气` | ❌ 搜到一首叫《今天》的歌 |

所以工具描述里已明确要求模型**先提取关键词**，端到端实测有效
（"今天上海天气怎么样" → 模型自动搜 `上海天气`）。

另一个局限：搜索**不抓取网页正文**，所以对"需要读页面才有答案"的问题
（精确数值、长文内容）无能为力。**这类需求应该像天气一样接专用 API**，
而不是指望搜索。模型被要求**拿不到就直说**，不许编。

### 其他说明

- 图片由 NapCat 直接拉取 URL 发送，不下载到服务器，`SEARCH_MAX_IMAGES` 控制上限（默认 3 张）
- 最多循环 `SEARCH_MAX_ROUNDS` 轮工具调用，防止模型反复搜索停不下来
- 关掉 `SEARCH_ENABLED` 则模型看不到搜索工具，退化为纯记忆回答

### 自检

```bash
# 纯逻辑 + 真实联网测试（Bing 免费）
docker exec nonebot python /app/scripts/test_tools.py
# 只测逻辑，不发网络请求
docker exec nonebot python /app/scripts/test_tools.py --offline
```

### 排障

| 现象 | 原因 |
|---|---|
| 搜出来是空的 | Bing 的 RSS **必须**带 `Accept: application/rss+xml`，用默认的 `*/*` 会返回 200 + 空 feed |
| 结果毫不相关 | 查询太长。检查模型是否正确提取了关键词 |
| 图片发不出来 | 看 `docker compose logs nonebot \| grep 发送图片失败`，多为图片直链被防盗链 |

---

## 3.7 今日运势

```
/运势            看自己的
/运势 @某人       看别人的（同一天谁查都一样）
/jrrp            别名
```

示例输出：

```
🙂 今日运势 · 阿伟 · 10月6日

【吉】★★★★☆ 72%
宜：清购物车、把拖了很久的小事做掉
忌：相信「下次一定」、冲动消费
幸运数字：69
幸运颜色：丁香紫

今天适合稳一点，把手上没做完的先收尾。
```

**核心设计：同一个人在同一天，结果完全固定。** 用 `sha256("fortune:{QQ号}:{日期}")`
作随机种子，跨天自动变化。要是用真随机数，连查两次结果不同，就不像"运势"而像随机数了。

- **不调用大模型** —— 响应是即时的、零 token 消耗，也不会因模型抽风输出奇怪内容
- 等级权重刻意压低「凶」到 **4%**，别让人扫兴；幸运指数区间与等级联动自洽
- 时区固定北京时间跨天，避免服务器时区导致"凌晨换运"
- 文案池在 `src/plugins/fortune/core.py`，想加内容直接改那几个列表

**自检**：

```bash
docker exec nonebot python /app/scripts/test_fortune.py
```

会验证确定性、跨天变化、格式完整性、400 样本字段自洽，以及 20000 次抽样的权重分布。

---

## 3.8 Web 管理台

浏览器打开 **`http://127.0.0.1:18080/admin`**（先跑 `deploy\tunnel.bat` 开隧道）

```
┌─ 运行状态卡片 ─────────────────────────────┐
│ QQ连接 1234567890 │ 运行时长 │ 内存占用    │
│ 已加载插件 4      │ 人格 4   │ AI接口 已配置│
└───────────────────────────────────────────┘
  概览 │ 人格 │ 配置 │ 日志
```

| 标签 | 能做什么 |
|---|---|
| **概览** | QQ 连接状态、运行时长、内存、插件列表、**一键重启** |
| **插件商店** | 搜索 **942 个社区插件**（官方 registry），**一键安装/卸载**，装完**立即热加载生效** |
| **人格** | 增删改人格（名字/说明/温度/**提示词**），改完**立即生效**；看每个群/私聊当前用什么人格，直接在网页上换 |
| **配置** | 脱敏显示各项配置；**一键测 AI 接口**和**联网搜索**连通性 |
| **日志** | 实时滚动 NoneBot 日志（3 秒自动刷新） |

### 插件商店是怎么做到「持久化」的

社区那两个 WebUI 在容器里装插件，重建就没了。这里的做法：

```
管理台点「安装」
   ↓  pip install --target /app/data/pylibs <插件>
挂载卷 data/bot/pylibs/          ← 宿主机上，重建容器不丢
   ↓  模块名写进 data/bot/extra_plugins.json
   ↓  nonebot.load_plugin() 热加载          ← 不用重启就生效
```

**两个关键的坑，都踩过并修掉了：**

1. **不能 `pip install` 到 site-packages** —— 容器以 uid 1001 运行，那是 root 的目录，权限拒绝。所以只能装到挂载卷。

2. **挂载卷目录必须 `append` 到 `sys.path` 末尾，且不能判断目录是否存在**：
   - pip 会把这个插件的**整个依赖树**都装进来（连 `nonebot2`、`pydantic` 的副本都有，一个插件 22MB）。
     如果插到 `sys.path` 最前面，这些副本会把框架本身顶掉，**整个机器人崩溃**。
   - 首次启动时 `pylibs` 还不存在，如果写成 `if dir.is_dir(): sys.path.append(...)`，
     后面装的插件就永远 import 不到，热加载必然失败。

**已知局限**：卸载只删插件本身，它带进来的依赖会留在卷里（可能被别的插件共用）。
真在意空间就 `rm -rf ~/bot/data/bot/pylibs`（前提是清单已空）。

**安全提示**：装第三方插件等于在服务器上执行别人的代码，请自己判断可信度。
商店里 `is_official` 为真的插件来自 NoneBot 官方组织。

### 为什么不用社区的现成 WebUI

NoneBot 社区有两个方案（[NoneBot WebUI](https://webui.nbgui.top/)、[nonebot-plugin-manageweb](https://pypi.org/project/nonebot-plugin-manageweb/)），
但它们都是为**裸机部署**设计的——在线装插件、改 `.env` 都是改本机文件。
而本项目是 Docker 部署，**容器里改的东西重建就没了**，比没有还糟。

所以这里只做**能真正持久化**的事：

- 人格文件走挂载卷 `./data/bot/`，改完写回宿主机，**重建容器也不丢**
- 重启则是**直接退出进程**（`os._exit(1)`），交给 Docker 的 `restart: unless-stopped` 拉起
  —— 不需要挂载 docker socket（那等于给容器 root 权限）
- 整个页面**零外部依赖**：HTML/CSS/JS 全内联，不引 CDN，离线可用

### 安全

- **只绑定回环地址** `127.0.0.1:18080`，公网看不到，靠 SSH 隧道访问
- Token 鉴权 + HttpOnly Cookie，Token 取 `WEBADMIN_TOKEN`（未设置则回落 `ONEBOT_ACCESS_TOKEN`）
- 配置页里所有密钥都**脱敏显示**

### 测试

```bash
# 上传后在服务器上执行（需能访问 127.0.0.1:18080）
bash ~/bot/deploy/test-admin.sh         # 鉴权、各接口、连通性检测
bash ~/bot/deploy/test-admin-write.sh   # 写操作、保护性校验、真实重启
```

实测：**17 项全部通过**，重启耗时 **4 秒**且 QQ 自动重连。

---

## 3.9 读图（视觉输入）

**用户直接发图片，机器人看得懂。** 不需要任何额外配置——只要模型支持视觉。

```
你：[发一张照片]
  → 红底中间一个蓝色方块，挺简洁的。这是画的啥？

你：[发一张照片] 这是什么？
  → 一张纯红色背景的图，正中间有个蓝色方块。就这么简单，没别的了～

你：1+1 等于几？
  → 2          （不带图时走原来的纯文本路径，不受影响）
```

**实现方式**

```
QQ 消息里的 [CQ:image] 段
   ↓  容器自己下载（不把带时效 token 的 QQ 链接丢给供应商）
   ↓  base64 成 data URI，按 OpenAI 多模态格式放进 content 数组
   ↓  {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}
模型直接看图回答
```

**几个设计取舍**

| 决定 | 原因 |
|---|---|
| **自己下载再 base64** | QQ 图片链接带时效 token，且供应商侧不一定能访问腾讯 CDN |
| **历史里只存文本** | 图片不进上下文，省 token，也不会撑爆上下文窗口 |
| **限制 2 张 / 单张 4MB** | 每张图都算视觉 token，base64 还会膨胀 1/3 |
| **带图时给系统提示词加三条硬规则** | 见下，每一条都是实测踩坑加的 |

### ⚠️ 坑一：图片和问题常常是两条消息

**这是「只会分析元素、认不出内容」的真正原因，跟模型能力无关。**

用户习惯先发图、隔几秒再问「这是谁」，而 QQ 把这两条当成两个独立事件：

```
08:13:03  [图片]     → 第 1 次请求：模型看到图，但没看到问题 → 只能描述元素
08:13:05  '这是谁'   → 第 2 次请求：这条消息里根本没有图！
                       模型只能看到历史里的「[发了一张图片]」和上一条的元素描述，
                       于是只能复述元素，甚至拿「粉色头发 蓝眼睛 围巾」去搜（必然搜不到）
```

实测复现（把历史状态还原成用户那次的情况，同一张图同一个问题）：

| 处理方式 | 回复 |
|---|---|
| **不处理** | ❌ 说实话我认不出来…这种Q版小人二创太多了，我搜了一圈也没对上号 |
| **沿用图片** | ✅ 后藤一里，就是《孤独摇滚！》里的波奇酱。粉头发蓝眼睛… |

**修复（两个改动）**：

1. **粘性图片** —— 每个会话记住最近一张图，`LLM_STICKY_IMAGE_SECONDS`（默认 120 秒）内的
   纯文字消息自动沿用。用户先发图再问问题，问的那次也能看到图。
2. **改掉图片-only 的兜底提示** —— 原来是「看看这张图。」，这句等于什么都没问，
   模型只会描述元素。现在换成「请说明这是什么，如果是角色/作品/物品，直接说出名称和出处」，
   连第一条消息的回复都能直接报出名字。

> 日志里每次收图都会记录 `字节数 / MIME / 分辨率`（`_image_info` 直接解析文件头，
> 不依赖 Pillow）。识别率不对时先看这条，能立刻区分是「图没收到」还是「图太小」。

### ⚠️ 坑二：人格提示词会压制识图能力

**模型本身看图就能认出角色，不需要联网。** 但实测发现两个问题：

| 问题 | 现象 | 修法 |
|---|---|---|
| **人格带偏** | 「吐槽役」设定（爱开玩笑、阴阳怪气）会让模型**用外貌描述代替回答**——明明认出来了却说"我没认出来，八成是某粉毛角色" | 系统提示词里明写：**必须先说出识别到的名称和出处，不许用外貌描述代替回答，这一条优先于人格设定** |
| **拿描述去搜图** | 模型会调 `web_search` 搜「粉色头发 蓝色发饰 围巾」，文字搜索根本搜不到图；实测连搜 4 次、耗 40 秒然后**超时** | 明写：**辨认图片内容时不要调用 web_search** |
| **自认 OCR** | 问"这张图里有什么"会答"图中没有文字，请上传包含文字的图片" | 明写：**不要把自己当 OCR 工具，也不要说「图中没有文字」** |

修复前后的实测对比（同一张图，问"这是谁"）：

```
修复前  默认     → ✅ 后藤一里（波奇酱），出自《孤独摇滚！》
        猫娘     → ✅ 后藤一里，也就是波奇酱喵！
        严谨助手 → ❌ ReadTimeout（拿视觉特征搜了 4 次）
        吐槽役   → ❌ 说实话我没认出来，八成是同人画的某粉毛角色

修复后  默认     → ✅ 后藤一里（波奇酱），出自《孤独摇滚！》
        猫娘     → ✅ 这是后藤一里（波奇酱），出自《孤独摇滚！》喵～
        严谨助手 → ✅ 后藤一里（昵称"波奇酱"），附识别依据
        吐槽役   → ✅ 后藤一里（波奇酱）…这表情一看就是社恐当场去世（吐槽风格保留）
```

**结论：识图不需要联网，也不需要以图搜图。** 只要不让人格设定把事实性回答压住就行。

> 附：以图搜图服务实测——**trace.moe** 免费无需 Key，但**只索引动画正片**，
> 对插画/表情包无效（把波奇酱认成了《游戏三人娘》）；**SauceNAO** 更对口但要 API Key
> （匿名账号被拒）。本项目没接，因为模型自己的能力已经够用。

**配置**：

```ini
LLM_VISION_ENABLED=true      # 关掉则完全忽略用户发来的图片
LLM_MAX_IMAGES=2             # 单条消息最多处理几张
LLM_MAX_IMAGE_BYTES=4000000  # 单张图大小上限
```

> ⚠️ 换成**不支持视觉**的模型时，请把 `LLM_VISION_ENABLED` 设为 `false`，否则接口会报错。
> 实测在你当前套餐内可用的视觉模型：`deepseek/deepseek-v4.1-flash`（主力）、
> `Qwen/Qwen3.8-Omni-Flash`、`google/gemini-3.8-flash`。

**实测记录**（走插件自身的 `_ask_llm` 代码路径，非复刻请求）：

| 输入 | 回复 |
|---|---|
| 红底+蓝方块，"这张图里有什么？" | 一张纯红色背景的图，正中间有个蓝色方块 |
| 同上，"中间那块是什么颜色？" | 蓝色 |
| 同上，只发图不带文字 | 红底中间一个蓝色方块，挺简洁的。这是画的啥？ |
| 纯红图，"整体什么颜色？" | 偏橘的红色——有点像火焰红，大概 #FF3B1F 那种感觉 |

---

## 4. 从零部署到新服务器

```bash
# 1. 上传代码到 ~/bot（保持目录结构）
# 2. 环境准备：装 Docker + 加 4GB Swap + 关无用服务
sudo bash ~/bot/deploy/server-prep.sh
# 3. 配置国内 Docker 镜像源（阿里云直连 Docker Hub 会超时）
sudo bash ~/bot/deploy/docker-mirror.sh
# 4. 配置 .env
cp ~/bot/.env.example ~/bot/.env && vim ~/bot/.env && chmod 600 ~/bot/.env
# 5. 启动
cd ~/bot && docker compose up -d --build
# 6. 扫码登录：开 SSH 隧道 → WebUI → 扫码；登录后配置已自动生效
```

OneBot 反向 WS 配置已写在 `data/napcat/config/onebot11_<QQ号>.json` 里，
登录成功后 NapCat 会自动连上 NoneBot，**无需在 WebUI 手工添加**。

---

## 5. 资源占用（实测）

| 项目 | 数值 |
|---|---|
| napcat 容器 | **203 MiB**（上限 800 MiB） |
| nonebot 容器 | **48 MiB**（上限 256 MiB） |
| 系统可用内存 | **917 MiB** + 4 GiB Swap |
| 磁盘 | 镜像 2.14 GB |
| 已关闭的服务 | `multipathd`、`tuned`、`ModemManager`、`udisks2`（省约 73 MB） |

内存上限的作用：一旦机器人异常膨胀，**被限制/被杀的是容器，而不是你的 MySQL**。

**服务器现状**：Ubuntu 24.04.4 / 2 核 / 1.6 GiB + 4 GiB Swap，
另跑着两个线上站点（`myweb` 的 nginx + Spring Boot + MySQL、`arknights-pixel`），未受影响。

---

## 6. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 容器重启后要重新扫码 | 已加 `NAPCAT_ACCOUNT` 解决。若仍要求扫码，说明会话失效，扫一次即可 |
| QQ 掉线 / 被踢 | 看 `docker compose logs napcat`。避免频繁重启、避免短时间群发大量消息 |
| NoneBot 收不到消息 | ① `docker compose logs napcat` 里是否有 `WebSocket反向服务...已启动` ② Token 两侧是否一致 |
| 日志报 `Authorization Header is invalid` | Token 不匹配。改 `.env` 后要 `docker compose up -d nonebot` |
| 浏览器访问 `/onebot/v11/ws` 返回 404 | **正常现象**。WebSocket 路由不匹配普通 HTTP GET，不是故障 |
| 日志报 `Legacy project format found!` | `pyproject.toml` 的 `plugins` 写成了数组，2.5+ 要求 `[tool.nonebot.plugins]` 表格式 |
| 拉镜像超时 | 阿里云直连 Docker Hub 不通，跑 `deploy/docker-mirror.sh` |
| 本地 Windows `pwsh` 无法识别 | 是 PowerShell 7 的命令，Windows 默认只有 5.1，用 `powershell` 或直接双击 `.bat` |
| **WebUI 里头像改了但界面不更新** | NapCat WebUI 注册了 **Service Worker**（`/webui/sw.js`）并缓存站点状态。**只有删除该站点 Cookie 才彻底生效**：F12 → 应用程序 → 清除网站数据（勾上 Cookie），或设置里删 `127.0.0.1:6099` 的 Cookie，再刷新。只清「缓存的图像和文件」、或只注销 Service Worker，**都不够**。纯浏览器显示问题，机器人在 QQ 里的头像是新的 |
| 内存告急 | `docker stats` 看占用，必要时调 `docker-compose.yml` 里的 `mem_limit` |

---

## 7. 安全与合规

1. **专用小号**。个人号自动化违反腾讯用户协议，封号是常态。绝不要用主号。
2. **端口只绑回环**。WebUI 限制在 `127.0.0.1:6099`，公网不可见，无需改阿里云安全组。
3. **Token 必须保密**。`ONEBOT_ACCESS_TOKEN` 是唯一鉴权，`.env` 权限 600 且不入库。
4. **加冷却和限流**。`llm_chat` 内置每会话冷却与并发上限，这是保号手段而非可选项。
5. **不要用于商业服务**。有商用需求请走 QQ 开放平台 / 企业微信官方 API。
6. **数据合规**。若存储聊天记录，需遵守《个人信息保护法》的告知与最小化原则。

---

## 8. 下一步扩展方向

- **持久化上下文**：现在存在进程内存，重启即丢。需要长期记忆时换 Redis / PostgreSQL。
- **权限管理**：用 `nonebot.permission.SUPERUSER` 保护管理命令。
- **内容审核**：接入敏感词过滤或平台审核 API。
- **工具调用**：给 LLM 插件加 Function Calling，让机器人能查天气、点歌等。
- **备份**：定期备份 `data/ntqq`（登录态）与 `data/napcat/config`。

---

## 9. 验证记录

在 **Ubuntu 24.04.4 / Python 3.12 / x86_64** 实测，nonebot2 **2.5.0**、
nonebot-adapter-onebot **2.4.6**、NapCat **v4.18.30**（内置 QQ 9.9.33-52230）。

| 验证项 | 方法 | 结果 |
|---|---|---|
| 插件加载 | `python scripts/smoke_test.py` | ✅ `echo` / `llm_chat` 均成功 |
| 配置格式 | `tomllib` 解析，确认 2.5 新格式 | ✅ `plugins: {}`，无 legacy 警告 |
| 容器编排 | `docker compose up -d --build` | ✅ 两容器 Up，nonebot healthy |
| 反向 WS 握手 | 真实 WebSocket 握手（错误 token 对照） | ✅ 正确 token 通，错误 token 403 |
| **端到端收发** | QQ 私聊发送 `/ping` | ✅ **日志显示被 echo 插件处理并回复 pong** |
| 断线重连 | 重启 nonebot 容器 | ✅ 日志 `Bot 1234567890 connected` |
| **容器重启保号** | `docker compose up -d napcat`（优雅停止 11.5s） | ✅ 日志 `正在快速登录 1234567890`，**无需扫码**，断线仅 **9 秒** |
| 开机自启 | `systemctl is-enabled docker` + restart 策略 | ✅ enabled / unless-stopped |
| 内存隔离 | `docker stats` | ✅ napcat 203MiB、nonebot 48MiB，系统可用 917MiB |

**未验证**：群聊场景（需机器人进群）、LLM 聊天（需 API Key）、服务器重启后的自动恢复。
