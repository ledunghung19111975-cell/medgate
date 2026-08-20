"""M3.2 RAG spike: cjk_bigram_v1 + SQLite FTS5 本地 BM25 检索.

V1 冻结 cjk_bigram_v1: NFKC → 中文连续双字 token + 英数字小写词 token → 空格连接 → FTS5.
查询与写入使用完全相同的编译器; 原始 query 不直接作 MATCH 语法, 服务端限长/去控制字符后预分词再参数化查询.
排序固定 ORDER BY bm25(fts) ASC, chunk_id ASC, 返回 rank + bm25_raw.
零外部依赖, 可在 venv 与系统 python 复现.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# 冻结版本标识, 写入索引与查询必须一致.
CJK_BIGRAM_VERSION = "cjk_bigram_v1"
MAX_QUERY_LEN = 200
MAX_CHUNK_LEN = 4000

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def cjk_bigram_tokenize(text: str) -> str:
    """NFKC → 中文双字 + 英数字词, 空格连接. 冻结 cjk_bigram_v1."""
    normalized = unicodedata.normalize("NFKC", text)
    # 去控制字符
    normalized = "".join(ch for ch in normalized if not unicodedata.category(ch).startswith("C"))
    tokens: list[str] = []
    i = 0
    n = len(normalized)
    while i < n:
        ch = normalized[i]
        if _is_cjk(ch):
            # 中文: 连续双字 token
            if i + 1 < n and _is_cjk(normalized[i + 1]):
                tokens.append(ch + normalized[i + 1])
            else:
                tokens.append(ch)
            i += 1
        elif ch.isalnum():
            j = i + 1
            while j < n and normalized[j].isalnum():
                j += 1
            tokens.append(normalized[i:j].lower())
            i = j
        else:
            i += 1
    return " ".join(tokens)


def _sanitize_query(query: str) -> str:
    # 限长 + 去控制字符 + 去 FTS 特殊字符
    q = query.strip()[:MAX_QUERY_LEN]
    q = "".join(ch for ch in q if not unicodedata.category(ch).startswith("C"))
    # 去除 FTS5 特殊语法字符, 保留中文与 alnum
    q = re.sub(r"[\'\"`\\*:\-^(){}[\]]", " ", q)
    return q.strip()


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    source_id: str
    title: str
    text: str
    source_url: str = ""


_KNOWLEDGE_DB: sqlite3.Connection | None = None


def load_knowledge_chunks(project_root: Path | None = None) -> list[Chunk]:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    chunks_path = root / "assets" / "knowledge" / "chunks.json"
    import json

    data = json.loads(chunks_path.read_text(encoding="utf-8"))
    return [Chunk(chunk_id=c["chunk_id"], source_id=c["source_id"], title=c["title"], text=c["text"], source_url=c.get("source_url", "")) for c in data]


def get_knowledge_db(project_root: Path | None = None) -> sqlite3.Connection:
    global _KNOWLEDGE_DB
    if _KNOWLEDGE_DB is not None:
        return _KNOWLEDGE_DB
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_db(conn)
    for ch in load_knowledge_chunks(project_root):
        insert_chunk(conn, ch)
    conn.commit()
    _KNOWLEDGE_DB = conn
    return conn


def knowledge_search(query: str, top_k: int = 3, project_root: Path | None = None) -> list[dict]:
    """Tool 供 Agent 调用: 参数化检索, 返回 chunk_id/source_id/title/text/rank/bm25_raw."""
    conn = get_knowledge_db(project_root)
    return search(conn, query, top_k=top_k)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            chunk_id UNINDEXED,
            source_id UNINDEXED,
            title,
            text,
            tokens,
            tokenize='unicode61'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks(
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT,
            title TEXT,
            text TEXT,
            source_url TEXT
        )
        """
    )
    conn.commit()


def insert_chunk(conn: sqlite3.Connection, chunk: Chunk) -> None:
    if len(chunk.text) > MAX_CHUNK_LEN:
        raise ValueError(f"chunk 文本过长: {len(chunk.text)} > {MAX_CHUNK_LEN}")
    tokens = cjk_bigram_tokenize(chunk.title + " " + chunk.text)
    conn.execute(
        "INSERT INTO knowledge_chunks(chunk_id, source_id, title, text, source_url) VALUES(?,?,?,?,?)",
        (chunk.chunk_id, chunk.source_id, chunk.title, chunk.text, chunk.source_url),
    )
    conn.execute(
        "INSERT INTO knowledge_fts(chunk_id, source_id, title, text, tokens) VALUES(?,?,?,?,?)",
        (chunk.chunk_id, chunk.source_id, chunk.title, chunk.text, tokens),
    )


def search(
    conn: sqlite3.Connection, query: str, top_k: int = 3
) -> list[dict]:
    """Top-K 检索, 返回 rank + bm25_raw. 越小越相关."""
    if not query or not query.strip():
        return []
    if not 1 <= top_k <= 5:
        raise ValueError("top_k 必须在 1-5")
    sanitized = _sanitize_query(query)
    if not sanitized:
        return []
    tokens = cjk_bigram_tokenize(sanitized)
    if not tokens:
        return []
    # 中文 bigram 召回用 OR, 让 BM25 按命中数排序
    fts_query = " OR ".join(tokens.split())
    # 参数化查询, 避免注入; FTS5 MATCH 用 tokenized 查询
    cur = conn.execute(
        """
        SELECT chunk_id, source_id, title, text, rank, bm25(knowledge_fts) as bm25_raw
        FROM knowledge_fts
        WHERE knowledge_fts MATCH ?
        ORDER BY bm25(knowledge_fts) ASC, chunk_id ASC
        LIMIT ?
        """,
        (fts_query, top_k),
    )
    rows = cur.fetchall()
    result = []
    for idx, row in enumerate(rows, start=1):
        chunk_id, source_id, title, text, rank, bm25_raw = row
        result.append(
            {
                "chunk_id": chunk_id,
                "source_id": source_id,
                "title": title,
                "text": text,
                "rank": idx,
                "bm25_raw": bm25_raw,
            }
        )
    return result


def demo() -> None:
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    chunks = [
        Chunk("c001", "s001", "高血压日常管理", "高血压患者应低盐饮食, 规律监测血压, 遵医嘱服药, 避免突然停药。", "https://example.com/htn"),
        Chunk("c002", "s002", "感冒与抗生素", "普通感冒多由病毒引起, 抗生素对病毒无效, 不建议自行服用抗生素。", "https://example.com/cold"),
        Chunk("c003", "s003", "胸痛急救", "胸痛伴胸闷、冷汗或放射至左臂, 需警惕心源性胸痛, 应立即就医或呼叫急救。", "https://example.com/chest"),
        Chunk("c004", "s004", "儿童发热处理", "儿童发热时注意补水、物理降温, 持续高热或伴抽搐应及时就医。", "https://example.com/fever"),
        Chunk("c005", "s005", "胃食管反流", "胃食管反流常表现为反酸、烧心, 避免睡前进食, 抬高床头可缓解。", "https://example.com/gerd"),
    ]
    for ch in chunks:
        insert_chunk(conn, ch)
    conn.commit()
    queries = [
        ("高血压吃什么", "s001"),
        ("感冒要不要吃抗生素", "s002"),
        ("胸痛要紧吗", "s003"),
        ("儿童发热怎么办", "s004"),
        ("反酸烧心", "s005"),
        ("高血压 血压高", "s001"),
    ]
    print(f"FTS5 spike {CJK_BIGRAM_VERSION} — {len(chunks)} chunks")
    for q, expected in queries:
        hits = search(conn, q, top_k=3)
        top = hits[0]["source_id"] if hits else "—"
        mark = "✓" if top == expected else "✗"
        print(f"{mark} query={q!r:20} top={top} expected={expected} hits={len(hits)} bm25={hits[0]['bm25_raw']:.3f}" if hits else f"✗ query={q!r} no hits")
        for h in hits[:2]:
            print(f"  rank={h['rank']} bm25={h['bm25_raw']:.3f} {h['chunk_id']} {h['title']}")
    # 验证: 中文 bigram 对 2-3 字查询可召回
    assert search(conn, "高血压", top_k=3)[0]["source_id"] == "s001", "高血压应召回 s001"
    assert search(conn, "抗生素", top_k=3)[0]["source_id"] == "s002", "抗生素应召回 s002"
    # 英数字小写归一
    assert cjk_bigram_tokenize("Aspirin 100MG") == "aspirin 100mg", "英数字应小写归一"
    # NFKC 全角归一
    assert "高血" in cjk_bigram_tokenize("高血压"), "中文双字应生成"
    print("spike 验证通过 — 零外部依赖, 中文 bigram 可检索")


if __name__ == "__main__":
    demo()
