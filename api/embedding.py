import hashlib
import math
import re
import unicodedata


VECTOR_SIZE = 384


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def embed(text: str) -> list[float]:
    """Deterministic local feature hashing; no model or external API required."""
    clean = normalize(text)
    words = clean.split()
    features = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    features += [clean[i : i + 3] for i in range(max(0, len(clean) - 2))]
    vector = [0.0] * VECTOR_SIZE
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % VECTOR_SIZE
        sign = 1.0 if (raw >> 8) & 1 else -1.0
        vector[index] += sign
    magnitude = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / magnitude, 7) for value in vector]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

