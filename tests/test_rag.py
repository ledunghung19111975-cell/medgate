import sqlite3
import unittest

from medgate.rag import Chunk, cjk_bigram_tokenize, init_db, insert_chunk, search


class RAGTokenizerTest(unittest.TestCase):
    def test_cjk_bigram(self) -> None:
        self.assertIn("高血", cjk_bigram_tokenize("高血压"))
        self.assertIn("血压", cjk_bigram_tokenize("高血压"))
        self.assertEqual(cjk_bigram_tokenize("Aspirin 100MG"), "aspirin 100mg")
        # NFKC 全角归一
        self.assertEqual(cjk_bigram_tokenize("ＨＴＮ"), "htn")

    def test_empty_and_control(self) -> None:
        self.assertEqual(cjk_bigram_tokenize(""), "")
        self.assertEqual(cjk_bigram_tokenize("\x00\x01"), "")


class RAGFTSTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        init_db(self.conn)
        chunks = [
            Chunk("c001", "s001", "高血压", "高血压患者应低盐饮食", "https://example.com/1"),
            Chunk("c002", "s002", "感冒", "普通感冒多由病毒引起, 抗生素无效", "https://example.com/2"),
            Chunk("c003", "s003", "胸痛", "胸痛伴胸闷需警惕心源性胸痛", "https://example.com/3"),
        ]
        for ch in chunks:
            insert_chunk(self.conn, ch)
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def test_search_exact(self) -> None:
        hits = search(self.conn, "高血压", top_k=3)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["source_id"], "s001")
        self.assertIn("bm25_raw", hits[0])
        self.assertEqual(hits[0]["rank"], 1)

    def test_search_or_semantics(self) -> None:
        # "高血压吃什么" 含高血压, 应召回 s001
        hits = search(self.conn, "高血压吃什么", top_k=3)
        self.assertEqual(hits[0]["source_id"], "s001")

    def test_empty_query(self) -> None:
        self.assertEqual(search(self.conn, "", top_k=3), [])
        self.assertEqual(search(self.conn, "   ", top_k=3), [])

    def test_top_k_validation(self) -> None:
        with self.assertRaises(ValueError):
            search(self.conn, "高血压", top_k=0)
        with self.assertRaises(ValueError):
            search(self.conn, "高血压", top_k=6)
