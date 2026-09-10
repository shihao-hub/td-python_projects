import asyncio
import json

import httpx

from next_scene.llm import WRITE_SYSTEM, _config, build_scene_prompt
from next_scene.samples import SAMPLES


async def run_case(max_tokens, thinking):
    base_url, api_key, model = _config()
    sample = SAMPLES["悬疑"]
    direction = {
        "title": "藏信设饵见客",
        "action": "林晚秋把信锁进抽屉，平静地下楼待客，谎称整理遗物时一无所获。",
        "link": "承接信中陌生称呼与神秘来客。",
        "consequence": "母女开始互相设防。",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": WRITE_SYSTEM},
            {"role": "user", "content": build_scene_prompt(sample["previous"], sample.get("constraints", ""), direction, "")},
        ],
        "temperature": 0.7,
        "max_tokens": max_tokens,
    }
    if thinking is not None:
        payload["thinking"] = {"type": thinking}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(f"{base_url}/chat/completions", json=payload, headers=headers)
    print(f"--- max_tokens={max_tokens} thinking={thinking} -> http {response.status_code}")
    if response.status_code != 200:
        print(response.text[:300])
        return
    data = response.json()
    choice = data["choices"][0]
    message = choice.get("message", {})
    print("finish_reason:", choice.get("finish_reason"))
    print("content length:", len(message.get("content") or ""))
    print("reasoning length:", len(message.get("reasoning_content") or ""))
    print("usage:", json.dumps(data.get("usage", {}), ensure_ascii=False))


async def main():
    await run_case(4096, None)
    await run_case(8192, None)
    await run_case(4096, "disabled")


if __name__ == "__main__":
    asyncio.run(main())
