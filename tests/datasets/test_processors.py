# Copyright (c) Facebook, Inc. and its affiliates.
import os
import random
import tempfile
import unittest

import torch
from mmf.datasets.processors.bert_processors import MaskedMultiSentenceBertTokenizer
from mmf.datasets.processors.processors import (
    CaptionProcessor,
    EvalAIAnswerProcessor,
    MultiClassFromFile,
    MultiHotAnswerFromVocabProcessor,
    TransformerBboxProcessor,
)
from mmf.utils.configuration import load_yaml
from omegaconf import OmegaConf

from ..test_utils import compare_tensors


class TestDatasetProcessors(unittest.TestCase):
    def _get_config(self, path):
        path = os.path.join(os.path.abspath(__file__), path)
        config = load_yaml(os.path.abspath(path))
        return config

    def test_caption_processor(self):
        config = self._get_config("../../../mmf/configs/datasets/coco/defaults.yaml")
        captioning_config = config.dataset_config.coco
        caption_processor_config = captioning_config.processors.caption_processor

        vocab_path = os.path.join(
            os.path.abspath(__file__), "..", "..", "data", "vocab.txt"
        )
        caption_processor_config.params.vocab.type = "random"
        caption_processor_config.params.vocab.vocab_file = os.path.abspath(vocab_path)
        caption_processor = CaptionProcessor(caption_processor_config.params)

        tokens = [1, 4, 5, 6, 4, 7, 8, 2, 0, 0, 0]
        caption = caption_processor(tokens)

        # Test start, stop, pad are removed
        self.assertNotIn("<s>", caption["tokens"])
        self.assertNotIn("</s>", caption["tokens"])
        self.assertNotIn("<pad>", caption["tokens"])

        # Test caption is correct
        self.assertEqual(caption["caption"], "a man with a red helmet")

    def test_multi_hot_answer_from_vocab_processor(self):
        config = self._get_config("../../../mmf/configs/datasets/clevr/defaults.yaml")
        clevr_config = config.dataset_config.clevr
        answer_processor_config = clevr_config.processors.answer_processor

        # Test num_answers==1 case
        vocab_path = os.path.join(
            os.path.abspath(__file__), "..", "..", "data", "vocab.txt"
        )
        answer_processor_config.params.vocab_file = os.path.abspath(vocab_path)
        answer_processor = MultiHotAnswerFromVocabProcessor(
            answer_processor_config.params
        )
        processed = answer_processor({"answers": ["helmet"]})
        answers_indices = processed["answers_indices"]
        answers_scores = processed["answers_scores"]

        self.assertTrue(
            compare_tensors(answers_indices, torch.tensor([5] * 10, dtype=torch.long))
        )
        expected_answers_scores = torch.zeros(19, dtype=torch.float)
        expected_answers_scores[5] = 1.0
        self.assertTrue(compare_tensors(answers_scores, expected_answers_scores))

        # Test multihot when num answers greater than 1
        answer_processor_config.params.vocab_file = os.path.abspath(vocab_path)
        answer_processor_config.params.num_answers = 3
        answer_processor = MultiHotAnswerFromVocabProcessor(
            answer_processor_config.params
        )
        processed = answer_processor({"answers": ["man", "with", "countryside"]})
        answers_indices = processed["answers_indices"]
        answers_scores = processed["answers_scores"]
        self.assertTrue(
            compare_tensors(
                answers_indices,
                torch.tensor([2, 3, 15, 2, 3, 15, 2, 3, 15, 2], dtype=torch.long),
            )
        )
        expected_answers_scores = torch.zeros(19, dtype=torch.float)
        expected_answers_scores[2] = 1.0
        expected_answers_scores[3] = 1.0
        expected_answers_scores[15] = 1.0
        self.assertTrue(compare_tensors(answers_scores, expected_answers_scores))

        # Test unk
        processed = answer_processor({"answers": ["test", "answer", "man"]})
        answers_indices = processed["answers_indices"]
        answers_scores = processed["answers_scores"]
        self.assertTrue(
            compare_tensors(
                answers_indices,
                torch.tensor([0, 0, 2, 0, 0, 2, 0, 0, 2, 0], dtype=torch.long),
            )
        )
        expected_answers_scores = torch.zeros(19, dtype=torch.float)
        expected_answers_scores[2] = 1.0
        self.assertTrue(compare_tensors(answers_scores, expected_answers_scores))

    def test_evalai_answer_processor(self):
        evalai_answer_processor = EvalAIAnswerProcessor()

        # Test number
        processed = evalai_answer_processor("two")
        expected = "2"
        self.assertEqual(processed, expected)

        # Test article
        processed = evalai_answer_processor("a building")
        expected = "building"
        self.assertEqual(processed, expected)

        # Test tokenize
        processed = evalai_answer_processor("snow, mountain")
        expected = "snow mountain"
        self.assertEqual(processed, expected)

        # Test contractions
        processed = evalai_answer_processor("isnt")
        expected = "isn't"
        self.assertEqual(processed, expected)

        # Test processor
        processed = evalai_answer_processor("the two mountain's \t \n   ")
        expected = "2 mountain 's"
        self.assertEqual(processed, expected)

    def test_transformer_bbox_processor(self):
        import numpy as np

        config = {
            "params": {
                "bbox_key": "bbox",
                "image_width_key": "image_width",
                "image_height_key": "image_height",
            }
        }

        bbox_processor = TransformerBboxProcessor(config)
        item = {
            "bbox": np.array([[100, 100, 100, 100]]),
            "image_width": 100,
            "image_height": 100,
        }
        processed_box = bbox_processor(item)["bbox"]
        self.assertTrue(
            torch.equal(
                processed_box, torch.tensor([[1, 1, 1, 1, 0]], dtype=torch.float)
            )
        )

    def test_multi_class_from_file(self):
        f = tempfile.NamedTemporaryFile(mode="w", delete=False)
        f.writelines("\n".join(["abc", "bcd", "def", "efg"]))
        f.close()
        config = OmegaConf.create({"vocab_file": f.name})
        processor = MultiClassFromFile(config)

        output = processor({"label": "abc"})
        self.assertEqual(output["class_index"], 0)
        output = processor({"label": "efg"})
        self.assertEqual(output["class_index"], 3)
        output = processor("def")
        self.assertEqual(output["class_index"], 2)

        self.assertRaises(AssertionError, processor, {"label": "UNK"})
        os.unlink(f.name)

    def test_masked_multi_sentence_full_sentences(self):
        torch.manual_seed(1)
        random.seed(1)
        config = self._masked_multi_sentence_config("full_sentences")
        config = OmegaConf.create(config)
        masked_token_processor = MaskedMultiSentenceBertTokenizer(config)
        processed = masked_token_processor({"sentences": self._sample_sentences()})

        self.assertEquals(processed["tokens"][0], "[CLS]")
        self.assertEquals(self._filter_nonzero(processed["input_ids"])[-1], 102)
        self.assertEquals(processed["lm_label_ids"][9], 2157)
        self.assertEquals(processed["lm_label_ids"][-5], 1996)
        self.assertNotIn(1, processed["segment_ids"].tolist())
        self.assertEquals(
            len(self._filter_nonzero(processed["input_mask"])), len(processed["tokens"])
        )
        self.assertEquals(
            " ".join(processed["tokens"]),
            (
                "[CLS] ben is an inside view . on the [MASK] [MASK] there is a [MASK] ,"
                " in front of that [MASK] [MASK] a small table ."
                " at [MASK] back of this [SEP]"
            ),
        )

    def test_masked_multi_sentence_two_sentences(self):
        torch.manual_seed(1)
        random.seed(1)
        config = self._masked_multi_sentence_config("two_sentences")
        config = OmegaConf.create(config)
        masked_token_processor = MaskedMultiSentenceBertTokenizer(config)
        processed = masked_token_processor({"sentences": self._sample_sentences()})

        self.assertEquals(processed["tokens"][0], "[CLS]")
        self.assertEquals(self._filter_nonzero(processed["input_ids"])[-1], 102)
        self.assertEquals(processed["lm_label_ids"][9], 2157)
        self.assertEquals(processed["lm_label_ids"][-11], 2003)
        self.assertNotIn(1, processed["segment_ids"].tolist())
        self.assertEquals(
            len(self._filter_nonzero(processed["input_mask"])), len(processed["tokens"])
        )
        self.assertEquals(
            " ".join(processed["tokens"]),
            (
                "[CLS] ben is an inside view . on the [MASK] [MASK] there is a [MASK] ,"
                " in front of that [MASK] [MASK] a small table . [SEP]"
            ),
        )

    def test_masked_multi_sentence_rand_sentences(self):
        torch.manual_seed(1)
        random.seed(1)
        config = self._masked_multi_sentence_config("rand_sentences")
        config = OmegaConf.create(config)
        masked_token_processor = MaskedMultiSentenceBertTokenizer(config)
        processed = masked_token_processor({"sentences": self._sample_sentences()})

        self.assertEquals(processed["tokens"][0], "[CLS]")
        self.assertEquals(self._filter_nonzero(processed["input_ids"])[-1], 102)
        self.assertEquals(processed["lm_label_ids"][3], 2157)
        self.assertEquals(processed["lm_label_ids"][-10], 2067)
        self.assertNotIn(1, processed["segment_ids"].tolist())
        self.assertEquals(
            len(self._filter_nonzero(processed["input_mask"])), len(processed["tokens"])
        )
        self.assertEquals(
            " ".join(processed["tokens"]),
            (
                "[CLS] on the [MASK] [MASK] there is a couch ,"
                " in front of that there is a small [MASK] ."
                " [MASK] the [MASK] of this couch there is a frame is [SEP]"
            ),
        )

    def test_masked_multi_sentence_full_sentences_with_separator(self):
        torch.manual_seed(1)
        random.seed(1)
        config = self._masked_multi_sentence_config(
            "full_sentences", with_separator=True
        )
        config = OmegaConf.create(config)
        masked_token_processor = MaskedMultiSentenceBertTokenizer(config)
        processed = masked_token_processor({"sentences": self._sample_sentences()})

        self.assertEquals(processed["tokens"][0], "[CLS]")
        self.assertEquals(self._filter_nonzero(processed["input_ids"])[-1], 102)
        self.assertEquals(processed["lm_label_ids"][10], 2157)
        self.assertEquals(processed["lm_label_ids"][-3], 1996)
        self.assertNotIn(1, processed["segment_ids"].tolist())
        self.assertEquals(
            len(self._filter_nonzero(processed["input_mask"])), len(processed["tokens"])
        )
        self.assertEquals(
            " ".join(processed["tokens"]),
            (
                "[CLS] ben is an inside view ."
                " [SEP] on the [MASK] [MASK] there is a [MASK] , "
                "in front of that [MASK] [MASK] a small table ."
                " [SEP] at [MASK] back [SEP]"
            ),
        )

    def _masked_multi_sentence_config(
        self, multisentence_tokenizer_type, with_separator=False
    ):
        return {
            "tokenizer_config": {
                "type": "bert-base-uncased",
                "params": {"do_lower_case": True},
            },
            "mask_probability": 0.15,
            "max_seq_length": 32,
            "type": multisentence_tokenizer_type,
            "with_sentence_separator": with_separator,
        }

    def _filter_nonzero(self, list_to_filter):
        return list(filter(lambda x: x != 0, list_to_filter))

    def _sample_sentences(self):
        return [
            "This is an inside view.",
            (
                "On the right side there is a couch,"
                " in front of that there is a small table."
            ),
            (
                "At the back of this couch there is a frame"
                " is attached to the wall and also there is a lamp."
            ),
            "Beside that there is a door.",
            "On the left side I can see two chairs on the floor.",
            "In the background there is a table on which few objects are placed.",
            "On the above there is a mirror is attached to the wall.",
        ]
