import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import translate_pdf_parallel as parallel
import translate_pdf_via_codex as pipeline


class TranslationBatchTests(unittest.TestCase):
    def test_serial_batches_span_pages_while_parallel_batches_stay_on_each_page(self):
        pages = [[{"id": f"p{page:03d}b0001", "page": page, "block_index": 1,
                   "text": "This complete body paragraph explains the experimental results in detail.",
                   "xMin": 20, "yMin": 70, "xMax": 280, "yMax": 130}]
                 for page in (1, 2)]
        serial = pipeline.build_batches(pages, 7000, page_size=(300, 400))
        concurrent = parallel.build_page_batches(list(enumerate(pages, 1)), 7000,
                                                  page_size=(300, 400))
        self.assertEqual([[item["id"] for item in batch] for batch in serial],
                         [["p001b0001", "p002b0001"]])
        self.assertEqual([(batch.page_num, [item["id"] for item in batch.items]) for batch in concurrent],
                         [(1, ["p001b0001"]), (2, ["p002b0001"])])

    def test_low_overlap_cache_is_rejected_by_serial_and_retained_by_parallel(self):
        items = [{"id": f"p001b{index:04d}", "text": "Body paragraph."} for index in range(1, 6)]
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job = {"job_dir": root, "schema_path": root / "schema.json",
                       "translations_path": root / "translations.json"}
                job["translations_path"].write_text(json.dumps({"p001b0001": "缓存译文"}))
                requested = []

                def run(cmd, **kwargs):
                    payload = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])
                    requested.extend(item["id"] for item in payload["items"])
                    result = {"items": [{"id": item["id"], "translation": "新译文"}
                                        for item in payload["items"]]}
                    Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(result))
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                with patch("subprocess.run", side_effect=run):
                    if entry == "serial":
                        result = pipeline.translate_batches([items], job, model="test")
                    else:
                        cached = parallel.load_cached_translations(job["translations_path"],
                                                                   {item["id"] for item in items}, retranslate=False)
                        args = SimpleNamespace(model="test", reasoning_effort="low", retries=1, page_workers=1)
                        result = parallel.run_parallel_translation([parallel.PageBatch(1, 1, items)], cached, job, args)
                expected_requests = items if entry == "serial" else items[1:]
                self.assertEqual(requested, [item["id"] for item in expected_requests])
                self.assertEqual(result["p001b0001"], "新译文" if entry == "serial" else "缓存译文")
                self.assertEqual(json.loads(job["translations_path"].read_text()), result)

    def run_entry(self, entry, responses, *, stale_output=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = {"job_dir": root, "schema_path": root / "schema.json",
                   "translations_path": root / "translations.json"}
            items = [{"id": "p001b0001", "text": "First paragraph."}]
            requests = []

            def run(cmd, **kwargs):
                self.assertEqual(cmd[:2], ["codex", "exec"])
                self.assertIn("test-model", cmd)
                self.assertIn("First paragraph.", kwargs["input"])
                self.assertTrue(Path(cmd[cmd.index("--output-schema") + 1]).exists())
                output = Path(cmd[cmd.index("-o") + 1])
                response = responses[len(requests)]
                requests.append(cmd)
                returncode = 0
                if isinstance(response, tuple):
                    returncode, response = response
                if response is not None:
                    output.write_text(response, encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode, "stdout", "stderr")

            if stale_output is not None:
                prefix = "batch-01" if entry == "serial" else "page-001-chunk-01"
                (root / f"{prefix}.out.json").write_text(stale_output)
            with patch("subprocess.run", side_effect=run), patch("time.sleep"):
                if entry == "serial":
                    result = pipeline.translate_batches([items], job, model="test-model")
                else:
                    args = SimpleNamespace(model="test-model", reasoning_effort="low", retries=3, page_workers=2)
                    result = parallel.run_parallel_translation([parallel.PageBatch(1, 1, items)], {}, job, args)
            self.assertEqual(json.loads(job["translations_path"].read_text()), result)
            return result, len(requests)

    def test_both_entries_retry_invalid_payloads_before_caching(self):
        good = json.dumps({"items": [{"id": "p001b0001", "translation": "第一段。"}]})
        bad_payloads = [
            "not JSON", "[]", '{"items": []}',
            json.dumps({"items": [{"id": "p001b0001", "translation": "  "}]}),
            json.dumps({"items": [{"id": "p001b0001", "translation": "旧译文"}] * 2}),
            json.dumps({"items": [{"id": "p001b0001", "translation": None}]}),
            json.dumps({"items": [{"id": "unexpected", "translation": "额外译文"}]}),
        ]
        for entry in ("serial", "parallel"):
            for bad in bad_payloads:
                with self.subTest(entry=entry, payload=bad):
                    result, count = self.run_entry(entry, [bad, good])
                    self.assertEqual(result, {"p001b0001": "第一段。"})
                    self.assertEqual(count, 2)

    def test_both_entries_reject_stale_output_when_command_did_not_write(self):
        stale = json.dumps({"items": [{"id": "p001b0001", "translation": "旧译文"}]})
        fresh = json.dumps({"items": [{"id": "p001b0001", "translation": "新译文"}]})
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                result, count = self.run_entry(entry, [None, fresh], stale_output=stale)
                self.assertEqual(result, {"p001b0001": "新译文"})
                self.assertEqual(count, 2)

    def test_both_entries_fail_after_exhausting_invalid_responses(self):
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(RuntimeError, "translation.*failed"):
                    self.run_entry(entry, ["bad JSON"] * 3)

    def test_nonzero_command_exit_retries_even_if_output_looks_valid(self):
        rejected = json.dumps({"items": [{"id": "p001b0001", "translation": "失败结果"}]})
        accepted = json.dumps({"items": [{"id": "p001b0001", "translation": "成功结果"}]})
        for entry in ("serial", "parallel"):
            with self.subTest(entry=entry):
                result, count = self.run_entry(entry, [(1, rejected), accepted])
                self.assertEqual(result, {"p001b0001": "成功结果"})
                self.assertEqual(count, 2)

    def test_worker_count_does_not_change_translations_or_persisted_cache(self):
        for workers in (1, 2):
            with self.subTest(workers=workers), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                job = {"job_dir": root, "schema_path": root / "schema.json",
                       "translations_path": root / "translations.json"}
                batches = [parallel.PageBatch(page, 1, [{"id": f"p{page:03d}b0001", "text": "Body."}])
                           for page in (1, 2)]
                def run(cmd, **kwargs):
                    payload = json.loads(kwargs["input"].split("待翻译条目如下：", 1)[1])
                    result = {"items": [{"id": item["id"], "translation": "  正文。  "}
                                        for item in payload["items"]]}
                    Path(cmd[cmd.index("-o") + 1]).write_text(json.dumps(result))
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                args = SimpleNamespace(model="test", reasoning_effort="low", retries=1, page_workers=workers)
                with patch("subprocess.run", side_effect=run):
                    result = parallel.run_parallel_translation(batches, {}, job, args)
                expected = {"p001b0001": "正文。", "p002b0001": "正文。"}
                self.assertEqual(result, expected)
                self.assertEqual(json.loads(job["translations_path"].read_text()), expected)


if __name__ == "__main__":
    unittest.main()
