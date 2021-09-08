# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import json
import os
import pickle
import tarfile
from typing import List, Tuple

from joblib import Parallel, delayed
from tqdm import tqdm
from transformers import AutoTokenizer

import nemo.collections.nlp.data.text_normalization.constants as constants
from nemo.collections.nlp.data.text_normalization.utils import process_url
from nemo.utils import logging


def preprocess_file(input_file: str) -> List[Tuple[List[str]]]:
    """
    Peforms initial preprocessing, i.e., urls formatting, removal of "_trans" from Ru set

    Args:
        input_file: path to a file in google TN format

    Returns:
        Processed data. Each element is a Tuple(List[semiotic classes], List[written words], List[spoken words])
    """
    cur_split = []
    with open(input_file, 'r', encoding='utf-8') as f:
        # Loop through each line of the file
        cur_classes, cur_tokens, cur_outputs = [], [], []
        for linectx, line in tqdm(enumerate(f)):
            es = line.strip().split('\t')
            if len(es) == 2 and es[0] == '<eos>':
                # Update cur_split
                cur_outputs = process_url(cur_tokens, cur_outputs, lang)
                cur_split.append((cur_classes, cur_tokens, cur_outputs))
                # Reset
                cur_classes, cur_tokens, cur_outputs = [], [], []
                continue
            # Remove _trans (for Russian)
            if lang == constants.RUSSIAN:
                es[2] = es[2].replace('_trans', '')
            # Update the current example
            assert len(es) == 3
            cur_classes.append(es[0])
            cur_tokens.append(es[1])
            cur_outputs.append(es[2])
    return cur_split


def write_batches_to_tarfiles(
    input_file: str,
    tokenizer: AutoTokenizer,
    tokenizer_name: str,
    mode: str,
    max_seq_len: int,
    num_tokens: int,
    decoder_data_augmentation: bool = False,
    do_basic_tokenize: bool = False,
    max_insts: int = -1,
):
    """
    Writes current fragment of the overall parallel corpus to tarfiles by:
    (1) Creating a minibatches using a TranslationDataset object.
    (2) Writing each minibatch to a pickle file.
    (3) Adding pickle files to a tarfile until it reaches num_batches_per_tarfile.
    """

    dataset = TextNormalizationDecoderDataset(
        input_file=input_file,
        raw_instances=preprocess_file(input_file),
        tokenizer=tokenizer,
        tokenizer_name=tokenizer_name,
        mode=mode,
        max_len=max_seq_len,
        decoder_data_augmentation=decoder_data_augmentation,
        lang=lang,
        do_basic_tokenize=do_basic_tokenize,
        use_cache=False,
        max_insts=max_insts,
        do_tokenize=False,
    )

    dataset.batchify(args.tokens_in_batch)

    tar_file_ctr = 0
    tar_file_path = os.path.join(
        out_dir, 'fragment-%s-batches.tokens.%d.%d.tar' % (fragment_index, num_tokens, tar_file_ctr)
    )
    tar_file_ptr = tarfile.open(tar_file_path, 'w')
    total_batch_ctr = 0
    batch_ctr = 0
    for batch in dataset.batches:
        total_batch_ctr += 1
        batch_ctr += 1
        pickle_file = os.path.join(out_dir, 'fragment-%s-batch-%d.pkl' % (fragment_index, total_batch_ctr))

        pickle.dump(batch, open(pickle_file, 'wb'))
        tar_file_ptr.add(pickle_file)
        os.remove(pickle_file)

        if batch_ctr == num_batches_per_tarfile:
            tar_file_ctr += 1
            tar_file_ptr.close()
            tar_file_path = os.path.join(
                out_dir, 'fragment-%s-batches.tokens.%d.%d.tar' % (fragment_index, num_tokens, tar_file_ctr)
            )
            tar_file_ptr = tarfile.open(tar_file_path, 'w',)
            batch_ctr = 0

    # return tar files paths that have batches remaining
    remainder_tar_file_path = tar_file_ptr.name
    tar_file_ptr.close()

    return total_batch_ctr, remainder_tar_file_path


"""
python create_tarred_tn_dataset.py \
--fname="/mnt/sdb/DATA/normalization/google_data/DEL/output-00099-of-00100" \
--out_dir="/home/ebakhturina/NeMo/examples/nlp/duplex_text_normalization/tarred"
"""


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='NMT dataset pre-processing')
    parser.add_argument(
        '--tokenizer_model', type=str, default='yttm', help='Supports yttm, sentencepiece and HuggingFace tokenizers',
    )
    parser.add_argument('--transformer_name', type=str, default=None, help='Path to tokenizer model')
    parser.add_argument('--pkl_file_prefix', type=str, default='parallel', help='Prefix for tar and pickle files')
    parser.add_argument('--fname', type=str, required=True, help='Path to monolingual data file')
    parser.add_argument('--out_dir', type=str, required=True, help='Path to store dataloader and tokenizer models')
    parser.add_argument('--max_seq_length', type=int, default=512, help='Max Sequence Length')
    parser.add_argument('--min_seq_length', type=int, default=1, help='Min Sequence Length')
    parser.add_argument('--tokens_in_batch', type=int, default=16000, help='# Tokens per batch per GPU')
    # parser.add_argument(
    #     '--lines_per_dataset_fragment',
    #     type=int,
    #     default=1000000,
    #     help='Number of lines to consider for bucketing and padding',
    # )
    parser.add_argument(
        '--num_batches_per_tarfile',
        type=int,
        default=1000,
        help='Number of batches (pickle files) within each tarfile',
    )

    args = parser.parse_args()

    from nemo.collections.nlp.data.text_normalization.decoder_dataset import TextNormalizationDecoderDataset
    from glob import glob

    input_file = args.fname

    transformer_name = 'albert-base-v2'
    mode = "tn"
    max_seq_length = 512
    min_seq_length = 1
    decoder_data_augmentation = False
    lang = "en"
    do_basic_tokenize = False
    max_insts = -1
    os.makedirs(args.out_dir, exist_ok=True)
    out_dir = args.out_dir

    # check if exists do not proceed and re-use
    args.tokens_in_batch = 64
    tokens_in_batch = args.tokens_in_batch
    args.num_batches_per_tarfile = 20
    num_batches_per_tarfile = args.num_batches_per_tarfile
    n_jobs = -2
    fragment_index = 0
    tar_file_prefix = "tn"
    world_size = 1

    tokenizer = AutoTokenizer.from_pretrained(transformer_name)
    metadata_path = os.path.join(out_dir, f'metadata.tokens.{tokens_in_batch}.json')
    # add parallel with glob for all files

    # TODO provide a list of semiotic classes in the config
    # TODO shuffle data

    results_list = Parallel(n_jobs=n_jobs)(
        delayed(write_batches_to_tarfiles)(
            input_file=input_file,
            tokenizer=tokenizer,
            tokenizer_name=transformer_name,
            mode=mode,
            max_seq_len=max_seq_length,
            num_tokens=args.tokens_in_batch,
            decoder_data_augmentation=decoder_data_augmentation,
            do_basic_tokenize=do_basic_tokenize,
            max_insts=max_insts,
        )
        for input_file in [
            "/mnt/sdb/DATA/normalization/google_data/DEL/output-00099-of-00100",
            "/mnt/sdb/DATA/normalization/google_data/DEL/output-00098-of-00100",
        ]
    )

    # input_file = "/mnt/sdb/DATA/normalization/google_data/DEL/output-00099-of-00100"
    # results_list = write_batches_to_tarfiles(
    #     input_file=input_file,
    #     tokenizer=tokenizer,
    #     tokenizer_name=transformer_name,
    #     mode=mode,
    #     max_seq_len=max_seq_length,
    #     num_tokens=args.tokens_in_batch,
    #     decoder_data_augmentation=decoder_data_augmentation,
    #     do_basic_tokenize=do_basic_tokenize,
    #     max_insts=max_insts)

    """
    tar_file_prefix (str) : add string prefix to tar files 
    """
    # compute total batches so far

    total_batches = sum([batch_count for batch_count, _ in results_list])

    # save batches from tar files containing the left over batches (if there's enough batches)
    remainder_tar_file_ctr = 0
    remainder_tar_file_path = os.path.join(
        out_dir, f'remainder-batches.tokens.{tokens_in_batch}.tar_file_{remainder_tar_file_ctr}.tar'
    )
    remainder_tar_file_ptr = tarfile.open(remainder_tar_file_path, 'w')
    batch_in_tar_ctr = 0
    for _, tar_file_path in results_list:
        tar_file_ptr = tarfile.open(tar_file_path, 'r')
        for member in tar_file_ptr.getmembers():
            remainder_tar_file_ptr.addfile(member, tar_file_ptr.extractfile(member.name))
            batch_in_tar_ctr += 1
            if batch_in_tar_ctr == num_batches_per_tarfile:
                remainder_tar_file_ctr += 1
                remainder_tar_file_ptr.close()
                remainder_tar_file_path = os.path.join(
                    out_dir, f'remainder-batches.tokens.{tokens_in_batch}.tar_file_{remainder_tar_file_ctr}.tar',
                )
                remainder_tar_file_ptr = tarfile.open(remainder_tar_file_path, 'w',)
                batch_in_tar_ctr = 0
        tar_file_ptr.close()
        os.remove(tar_file_path)

    # log the number of batches remaining as they will be discarded
    num_batches_discarded = len(remainder_tar_file_ptr.getmembers())
    total_batches -= num_batches_discarded
    logging.info(f'Number of batches discarded: {num_batches_discarded}, total batches kept: {total_batches}')
    remainder_tar_file_ptr.close()
    os.remove(remainder_tar_file_path)

    # dump metadata to json
    metadata = {}
    metadata['num_batches'] = total_batches

    # rename tar files so they can be more easily used with CLI and YAML
    tar_file_paths = glob(f'{out_dir}/*.tar')
    for index, path in enumerate(tar_file_paths):
        os.rename(path, os.path.join(out_dir, f'{tar_file_prefix}.batches.tokens.{tokens_in_batch}.{index}.tar'))

    # add tar files to manifest
    tar_file_paths = glob(f'{out_dir}/*.tar')
    metadata['tar_files'] = tar_file_paths
    json.dump(metadata, open(metadata_path, 'w'))

    tar_file_paths = glob(f'{out_dir}/*.tar')

    num_tar_files = len(tar_file_paths)
    if num_tar_files < world_size:
        raise ValueError(
            (
                f'Number of tar files found: {num_tar_files} is less than world size: {world_size}. '
                f'There should be at least one tar file per GPU (ideally many tar files per GPU). '
                f'This may be due to dataset size. '
                f'Decrease num_batches_per_tarfile or num_tokens_per_batch to increase the number of tarfiles. '
                f'Also using shard_strategy=replicate will use all available tarfiles for every GPU. '
            )
        )
