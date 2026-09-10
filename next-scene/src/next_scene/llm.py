import json
import os
import re

import httpx

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/coding/paas/v4"
DEFAULT_MODEL = "glm-5.3"
DIRECTION_FIELDS = ("title", "action", "link", "consequence")

SUGGEST_SYSTEM = """你是一名资深中文小说编辑，擅长帮作者突破卡文。用户会提供一段刚写完的前文、当前的卡点，以及可选的必须保留的设定。你的任务是提出 3 个接下来可以写的发展方向。

要求：
1. 三个方向必须在人物行动或冲突推进方式上有实质区别，不能是同一思路的三种说法。
2. 每个方向必须承接前文已有的人物、动机、悬念和情绪，不得与前文矛盾。
3. 方向要具体到"谁在接下来一场戏里做什么"，避免"揭示真相""深化主题"这类空泛表述。
4. 若提供了必须保留的设定，所有方向都必须遵守。
5. 只输出 JSON，不输出任何其他文字。

输出格式：
{"directions": [{"title": "12 字以内的标题", "action": "接下来一场戏中人物的具体行动，80 字以内", "link": "这个方向如何承接前文的关键信息，60 字以内", "consequence": "按此发展会给后续故事带来的主要后果或新问题，60 字以内"}]}"""

WRITE_SYSTEM = """你是一名中文小说作者的合作写手。请根据前文、选定的发展方向和作者的调整意见，写出紧接着前文的下一场戏。

要求：
1. 只写一场戏，不要大纲、不要多个场景，篇幅约 600 至 1000 字。
2. 直接承接前文的时间、地点、人物状态和叙事语气，开头不要复述前文。
3. 用具体的动作、对话和细节推进，避免概括性叙述。
4. 人物言行与前文动机一致，严格遵守必须保留的设定和调整意见。
5. 直接输出正文，不要标题、序号或任何说明文字。"""


class LLMError(Exception):
    pass


def _load_dotenv(path=".env"):
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as file:
        for raw in file:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _config():
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    return base_url, api_key, model


async def chat(messages, temperature=0.7, max_tokens=4096, timeout=120.0):
    base_url, api_key, model = _config()
    if not api_key:
        raise LLMError("未配置 LLM_API_KEY，请在环境变量或 .env 文件中设置")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    thinking = os.environ.get("LLM_THINKING", "").strip().lower()
    if thinking in {"enabled", "disabled"}:
        payload["thinking"] = {"type": thinking}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as error:
            raise LLMError("模型请求超时，请稍后重试") from error
        except httpx.HTTPError as error:
            raise LLMError(f"网络错误：{error}") from error
    if response.status_code != 200:
        raise LLMError(f"模型服务返回 {response.status_code}：{response.text[:200]}")
    data = response.json()
    try:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        finish_reason = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError(f"模型响应格式异常：{json.dumps(data, ensure_ascii=False)[:200]}") from error
    if not content or not content.strip():
        if finish_reason == "length":
            raise LLMError("模型的思考耗尽了输出长度预算，请重试或设置 LLM_THINKING=disabled")
        raise LLMError("模型返回了空内容，请重试")
    return content


def extract_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        stripped = re.sub(r"\s*```\s*$", "", stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            text = stripped
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    raise LLMError("无法从模型输出中解析出 JSON")


def normalize_directions(data):
    if isinstance(data, dict):
        items = data.get("directions") or data.get("options") or []
    elif isinstance(data, list):
        items = data
    else:
        raise LLMError("方向数据格式不符合约定")
    result = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        direction = {field: str(item.get(field, "")).strip() for field in DIRECTION_FIELDS}
        if direction["title"] and direction["action"]:
            result.append(direction)
    if not result:
        raise LLMError("模型没有返回可用的方向")
    return result


def build_suggest_prompt(previous, stuck, constraints=""):
    parts = [f"【前文】\n{previous}", f"【卡点】\n{stuck}"]
    if constraints:
        parts.append(f"【必须保留的设定】\n{constraints}")
    return "\n\n".join(parts)


def build_scene_prompt(previous, constraints, direction, adjustment=""):
    parts = [f"【前文】\n{previous}"]
    if constraints:
        parts.append(f"【必须保留的设定】\n{constraints}")
    parts.append(
        "【选定的发展方向】\n"
        f"标题：{direction.get('title', '')}\n"
        f"行动：{direction.get('action', '')}\n"
        f"承接：{direction.get('link', '')}\n"
        f"后果：{direction.get('consequence', '')}"
    )
    if adjustment:
        parts.append(f"【调整意见】\n{adjustment}")
    return "\n\n".join(parts)


async def suggest_directions(previous, stuck, constraints=""):
    messages = [
        {"role": "system", "content": SUGGEST_SYSTEM},
        {"role": "user", "content": build_suggest_prompt(previous, stuck, constraints)},
    ]
    last_error = None
    for _ in range(2):
        content = await chat(messages, temperature=0.85, max_tokens=4096)
        try:
            return normalize_directions(extract_json(content))
        except LLMError as error:
            last_error = error
    raise LLMError(f"方向生成失败：{last_error}")


async def write_scene(previous, constraints, direction, adjustment=""):
    messages = [
        {"role": "system", "content": WRITE_SYSTEM},
        {"role": "user", "content": build_scene_prompt(previous, constraints, direction, adjustment)},
    ]
    content = await chat(messages, temperature=0.7, max_tokens=4096)
    return content.strip()
