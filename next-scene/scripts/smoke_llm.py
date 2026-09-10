import asyncio
import json

from next_scene.llm import suggest_directions, write_scene
from next_scene.samples import SAMPLES


async def main():
    sample = SAMPLES["悬疑"]
    directions = await suggest_directions(
        sample["previous"], sample["stuck"], sample.get("constraints", "")
    )
    print("=== 发展方向 ===")
    print(json.dumps(directions, ensure_ascii=False, indent=2))
    scene = await write_scene(
        sample["previous"],
        sample.get("constraints", ""),
        directions[0],
        "对白再克制一点，结尾留一个钩子",
    )
    print("\n=== 下一场戏 ===")
    print(scene)
    print(f"\n字数：{len(scene)}")


if __name__ == "__main__":
    asyncio.run(main())
