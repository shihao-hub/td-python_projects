"""按 chunk 分组切分数据集：同一块生成的样本必须落在同一 split，防止评测泄漏。"""

from __future__ import annotations

import hashlib
import random
from collections import defaultdict


def split_grouped(
    samples: list[dict],
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
    seed: int = 42,
) -> tuple[list[dict], list[dict], list[dict], dict]:
    """返回 (train, val, test, manifest)。"""
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"ratios 之和必须为 1，当前 {ratios}")

    groups: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        key = sample.get("chunk_id") or f"nogroup-{sample['id']}"
        groups[key].append(sample)

    keys = sorted(groups.keys())
    random.Random(seed).shuffle(keys)

    n = len(keys)
    n_train = max(1, round(n * ratios[0]))
    n_val = max(1, round(n * ratios[1])) if n >= 3 else 0
    train_keys = set(keys[:n_train])
    val_keys = set(keys[n_train : n_train + n_val])
    test_keys = set(keys[n_train + n_val :])

    train = [s for k in train_keys for s in groups[k]]
    val = [s for k in val_keys for s in groups[k]]
    test = [s for k in test_keys for s in groups[k]]

    # 保证 test 非空（小数据集时优先从 train 挪一组过去）
    if not test and train:
        victim = train[-1]
        test = [victim]
        train = train[:-1]

    def _count(split: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for s in split:
            counts[s["type"]] = counts.get(s["type"], 0) + 1
        return counts

    manifest = {
        "seed": seed,
        "ratios": list(ratios),
        "groups_total": n,
        "train": {"samples": len(train), "by_type": _count(train)},
        "val": {"samples": len(val), "by_type": _count(val)},
        "test": {"samples": len(test), "by_type": _count(test)},
    }
    return train, val, test, manifest


def group_fingerprint(sample_ids: list[str]) -> str:
    """split 内容指纹，写进 manifest 便于追踪数据版本。"""
    digest = hashlib.sha1("\n".join(sorted(sample_ids)).encode("utf-8")).hexdigest()
    return digest[:12]
