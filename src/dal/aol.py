import json
import os
from os.path import isfile, join
import numpy as np
import pandas as pd
import ir_datasets
from tqdm import tqdm
from pyserini.search.lucene import LuceneSearcher

import param

tqdm.pandas()
dataset = ir_datasets.load("aol-ia")
searcher = ""

def fetch_content(index_item, doc):
    if index_item == 'title':
        return doc.title
    else:
        return ' '.join([doc.title, doc.url])


def to_txt(pid):
    # The``docid`` is overloaded: if it is of type ``str``, it is treated as an external collection ``docid``;
    # if it is of type ``int``, it is treated as an internal Lucene``docid``. # stupid!!
    global searcher
    try:
        if not searcher.doc(pid):
            return " "
        else:
            return json.loads(searcher.doc(str(pid)).raw())['contents'].lower()
    except Exception as e:
        raise e


def initiate_queries_qrels(input):
    """
    :param input: location to store the files
    :return: creates a duplicate and no duplicate qrels and query file  from a dataframe
    """

    # loop to create qrels file format - qid iter did rel
    if not (isfile(join(input, 'qrels.tsv'))):
        qrels = {'qid': list(), 'iter': list(), 'pid': list(), 'rel': list()}
        print(f'creating qrels file in {input}')
        for qrel in tqdm(dataset.qrels_iter(), total=19442629):
            qrels['qid'].append(qrel.query_id)
            qrels['iter'].append(qrel.iteration)
            qrels['pid'].append(qrel.doc_id)
            qrels['rel'].append(qrel.relevance)
        qrels_df = pd.DataFrame.from_dict(qrels)
        qrels_df.to_csv(f'{input}/qrels.tsv', sep='\t', encoding='UTF-8', index=False, header=False)
        print('qrels file is ready for use')
    if not (isfile(join(input, 'queries.tsv'))):
        queries = {'id': list(), 'query': list()}
        print(f'creating queries file in {input}')
        for query in tqdm(dataset.queries_iter(), total=9966939):
            queries['id'].append(query.query_id)
            queries['query'].append(query.text)
        queries_df = pd.DataFrame.from_dict(queries)
        queries_df.to_csv(f'{input}/queries.tsv', sep='\t', encoding='UTF-8', index=False, header=False)
        queries_df.dropna(inplace=True)
        queries_df.drop_duplicates(inplace=True)
        queries_df.drop(queries_df.loc[(queries_df['query'] == "") | (queries_df['query'] == " ")].index, inplace=True)
        queries_df.to_csv(f'{input}/queries.clean.tsv', sep='\t', encoding='UTF-8', index=False, header=False)
        #create toy queries
        # toy_sample = queries_df.sample(n=500)
        # toy_sample.to_csv(f'{input.replace("aol", "toy.aol")}/queries.nodups.tsv', sep='\t', encoding='UTF-8',
        #                   index=False, header=False)
        print('queries file is ready for use')


def create_json_collection(input, index_item):
    """
    logic for this code was taken from https://github.com/castorini/anserini-tools/blob/7b84f773225b5973b4533dfa0aa18653409a6146/scripts/msmarco/convert_collection_to_jsonl.py
    :param index_item: defaults to title_and_text, use the params to create specified index
    :param input: folder name to create docs
    :return: collection of jsonl
    """


    if not os.path.isdir(os.path.join(input, index_item)): os.makedirs(os.path.join(input, index_item))
    if not isfile(join(input, index_item, 'docs00.json')):
        # added recently : remove qrel rows whose, qid have empty passage
        qrels = pd.read_csv(f'{input}/qrels.tsv', sep='\t', names=['qid', 'did', 'pid', 'rel'])
        empty_pid = set()
        max_docs_per_file = 1000000
        file_index = 0
        print(f'Converting aol docs into jsonl collection for {index_item}')
        for i, doc in enumerate(dataset.docs_iter()):  # doc returns doc_id, title, text, url, ia_url
            doc_id, doc_content = doc.doc_id, fetch_content(index_item, doc)
            if i % max_docs_per_file == 0:
                if i > 0:
                    output_jsonl_file.close()
                output_path = join(input, index_item, 'docs{:02d}.json'.format(file_index))
                output_jsonl_file = open(output_path, 'w', encoding='utf-8', newline='\n')
                file_index += 1
            # the reason to check for length less than 2 is because we get a merge with space added for urls and title merge
            if len(doc_content) < param.settings["aol"]["filter"][1]: empty_pid.add(doc_id)
            output_dict = {'id': doc_id, 'contents': doc_content}
            output_jsonl_file.write(json.dumps(output_dict) + '\n')
            if i % 100000 == 0:
                print(f'Converted {i:,} docs, writing into file {file_index}')
        qrels.drop_duplicates(subset=['qid', 'pid'], inplace=True)
        qrels = qrels[(qrels.pid.isin(empty_pid) == False)]
        #qrels with no duplicate pid and qid and also no pid's that are empty
        qrels.to_csv(f'{input}/qrels.{index_item}.clean.tsv', sep='\t', encoding='UTF-8', index=False, header=False)
    print('completed writing to file!')


def to_pair(input, output, index_item, cat=True):
    global searcher
    searcher = LuceneSearcher(param.settings['aol'][
                                  'index'] + index_item)
    if not searcher: raise ValueError(
        f'Lucene searcher cannot find/build aol index at {param.settings["aol"]["index"]}!')
    queries = pd.read_csv(f'{input}/queries.clean.tsv', sep='\t', index_col=False, names=['qid', 'query'],
                          converters={'query': str.lower}, header=None)
    qrels = pd.read_csv(f'{input}/qrels.{index_item}.clean.tsv', encoding='UTF-8', sep='\t',
                        index_col=False, names=['qid', 'iter', 'pid', 'relevancy'], usecols=['qid', 'pid', 'iter'], header=None)
    queries_qrels = pd.merge(queries, qrels, on='qid', how='inner', copy=False)
    doccol = 'docs' if cat else 'doc'
    del queries
    del qrels
    # queries_qrels['ctx'] = ''
    queries_qrels = queries_qrels.astype('category')
    queries_qrels[doccol] = queries_qrels['pid'].progress_apply(to_txt)
    if cat: queries_qrels = queries_qrels.groupby(['qid', 'query'], as_index=False, observed=True).agg({'iter': list, 'pid': list, doccol: ' '.join})
    #dropping empty rows that have a low doccol string length
    queries_qrels = queries_qrels[queries_qrels[doccol].str.len() >= param.settings["aol"]["filter"][1]]
    queries_qrels.to_csv(output, sep='\t', encoding='utf-8', index=False)
    return queries_qrels
