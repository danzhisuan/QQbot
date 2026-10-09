"""llm_chat 插件的配置。

字段名大写即为环境变量名，例如 llm_api_key -> LLM_API_KEY。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from nonebot import get_plugin_config


class Config(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # ---------- LLM 接口 ----------
    llm_api_base: str = "https://api.commandcode.ai/provider/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek/deepseek-v4.1-flash"
    llm_temperature: float = 0.8
    llm_timeout: float = 60.0

    # ---------- 对话行为 ----------
    # 兜底系统提示词：只在人格文件读取失败时使用
    llm_system_prompt: str = (
        "你是一个 QQ 群里的聊天机器人。回答要简短、自然、口语化，"
        "尽量控制在 100 字以内，不要使用 Markdown 标题和表格。"
    )
    # 同一会话两次回复之间的最小间隔（秒），保号用
    llm_cooldown_seconds: int = 5
    # 保留的历史消息条数（user + assistant 合计）
    llm_max_history: int = 6
    # 同时进行的 LLM 请求上限，超出直接丢弃，避免排队堆积
    llm_max_concurrency: int = 2

    # ---------- 客观信息注入 ----------
    # 这两项注入的不是"人设"，而是模型无从得知的客观事实，关掉会出问题：
    #   - 日期：模型不知道今天几号，涉及"最新/今天"的回答会跑偏
    #   - 型号：模型不知道自己在被谁调用，问它用什么模型只会含糊其辞
    llm_inject_date: bool = True
    llm_reveal_model: bool = True

    # ---------- 人格（persona） ----------
    # 人格定义文件。相对路径基于工作目录：
    #   容器内 cwd=/app，且 ./data 是挂载卷 -> /app/data/personas.json
    #   即宿主机 ~/bot/data/bot/personas.json，改完即时生效，无需重建镜像
    llm_persona_file: str = "data/personas.json"
    # 各会话当前人格的持久化文件（重启不丢）
    llm_persona_state_file: str = "data/persona_state.json"
    # 默认人格名；「/人格 默认」永远回到它
    llm_persona_default: str = "默认"
    # True  = 群里任何人都能切换人格
    # False = 仅机器人管理员（SUPERUSERS）和群主/群管理员
    llm_persona_open: bool = False

    # ---------- 读图（视觉输入） ----------
    # 需要模型支持视觉。当前主力模型 deepseek/deepseek-v4.1-flash 支持。
    # 换成不支持视觉的模型时，开着这个会让接口报错。
    llm_vision_enabled: bool = True
    # 单条消息最多处理几张图（每张都算视觉 token，别设太大）
    llm_max_images: int = 2
    # 单张图大小上限（字节）。base64 后还会膨胀约 1/3，别设太大
    llm_max_image_bytes: int = 4_000_000
    # 「粘性图片」窗口（秒）。
    # 用户经常把图片和问题分成两条消息发（先发图，隔几秒再问「这是谁」）。
    # 不处理的话，问问题那条消息里根本没有图，模型只能照着历史里的元素描述复述，
    # 表现就是「只会分析元素、认不出内容」。开启后，这段时间内的纯文字消息
    # 会自动沿用最近那张图。设为 0 关闭。
    llm_sticky_image_seconds: int = 120
    # 调试：把收到的图片原样存到 data/bot/received/，用于排查识别率问题。
    # 平时保持 false，免得白占磁盘。
    llm_debug_save_images: bool = False

    # 识图模型：留空则用 llm_model。
    # 有些模型看得更准（如 moonshotai/Kimi-K2.5），可以单独指定。
    llm_vision_model: str = ""
    # 识图专用接口，留空则复用上面的 llm_api_base / llm_api_key。
    # 用途：让识图走另一家供应商（例如阿里云百炼的免费额度），
    # 文本对话仍留在原来的接口上，两边互不影响。
    #   百炼 OpenAI 兼容地址：https://dashscope.aliyuncs.com/compatible-mode/v1
    llm_vision_api_base: str = ""
    llm_vision_api_key: str = ""
    # 极简模式：带图时只发「用户这一条消息 + 图片」，不带 system 提示词、不带历史。
    # 实测百炼 qwen3-vl 收到任何指令性文字就不看图、开始编角色名。
    # 若换成别的视觉模型（如 gemini）且想保留人格语气，可设为 false。
    llm_vision_minimal_prompt: bool = True

    # ---------- 酒馆（角色卡扮演） ----------
    # 角色卡文件（挂载卷，重建容器不丢）。管理台可编辑，也支持导入 ST 的 JSON 卡。
    llm_tavern_cards_file: str = "data/tavern_cards.json"
    # 各会话的激活状态与当前角色
    llm_tavern_state_file: str = "data/tavern_state.json"
    # 剧本库（剧情大纲 + 建议人设 + 关联角色卡）
    llm_tavern_scripts_file: str = "data/tavern_scripts.json"
    # 用户人设（"我是谁"）与剧情大纲
    llm_tavern_profile_file: str = "data/tavern_profile.json"
    # 世界书：关键词触发的设定注入
    llm_tavern_world_file: str = "data/tavern_world.json"
    # 一轮对话最多注入几条世界观（越多越费 token）
    llm_tavern_world_limit: int = 5
    # 酒馆模式保留的历史条数。角色扮演需要更长的上下文才连贯，
    # 但每多一条都在烧 token，按自己钱包调。
    llm_tavern_max_history: int = 20
    # 酒馆模式用哪个模型，留空则用 llm_model。
    # 角色扮演想要更好的文笔可以在这换成更强的模型。
    llm_tavern_model: str = ""
    # 酒馆模式温度，留空则用 llm_temperature。扮演通常比问答需要更高的随机性。
    llm_tavern_temperature: float = 0.9

    # ---------- 联网搜索 ----------
    # 总开关。关掉后模型看不到搜索工具，退化为纯记忆回答。
    search_enabled: bool = True
    # 搜索后端：auto / bing / bocha
    #   bing   免费，无需 Key（默认）
    #   bocha  博查 API，中文质量更好，需 Key
    #   auto   有 BOCHA_API_KEY 就用 bocha，否则用 bing
    search_provider: str = "auto"
    # 博查 API Key，去 https://open.bocha.cn 注册获取。留空则走免费的 Bing。
    bocha_api_key: str = ""
    # 单次搜索返回的网页条数
    search_count: int = 5
    # 搜索请求超时（秒）
    search_timeout: float = 20.0
    # 一次回复最多发送几张图片
    search_max_images: int = 1
    # 工具调用最多循环几轮，防止模型陷进去反复搜
    search_max_rounds: int = 3


plugin_config = get_plugin_config(Config)
