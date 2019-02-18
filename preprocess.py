import bisect
import re
from collections import defaultdict, OrderedDict

import numpy as np
import pandas as pd
from sklearn.feature_selection import SelectKBest, chi2
import swifter

from data_loader import load_data
from constants import LANGUAGES, UNICODE_BLOCKS, BLOCK_STARTING_POSITIONS, BLOCK_ORDER, ABC_LOWER, ACCENTS_LOWER


def _get_unicode_block(c):
    block_index = bisect.bisect(BLOCK_STARTING_POSITIONS, ord(c)) - 1
    block_starting_point = BLOCK_STARTING_POSITIONS[block_index]
    return UNICODE_BLOCKS[block_starting_point]


def _get_block_counts_dict(s):
    block_counts = defaultdict(lambda: 0)
    for c in s:
        block_name = _get_unicode_block(c)
        block_counts[block_name] += 1
    return block_counts


def _get_block_proportions_series(s):
    block_counts_dict = _get_block_counts_dict(s)
    block_counts = np.zeros(len(UNICODE_BLOCKS), dtype=np.uint8)
    for block_name, occurrences_in_block in block_counts_dict.items():
        block_counts[BLOCK_ORDER[block_name]] = occurrences_in_block

    # calculate proportions instead of absolute values
    block_proportions = block_counts / block_counts.sum()

    return pd.Series(block_proportions)


def create_unicode_block_proportions_feature(df):

    # create a new dataframe with all unicode block names as features
    block_proportions = df['tweet_text'].swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(_get_block_proportions_series)
    block_proportions.columns = UNICODE_BLOCKS.values()

    # remove all-zero blocks
    block_proportions = block_proportions.loc[:, (block_proportions != 0).any(axis=0)]

    return pd.concat([df, block_proportions], axis=1)


def reduce_lengthening(tweets):
    """
    Replace repeated character sequences of length 3 or greater with sequences
    of length 3.
    """
    pattern = re.compile(r"(.)\1+")
    without_repetitions = tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(lambda t: re.sub(pattern, r'\1\1', t))
    return without_repetitions


def remove_handles(tweets):

    pattern = r'(^|[^@\w])@(\w{1,15})\b'
    without_handles = tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(lambda t: re.sub(pattern, '', t))

    return without_handles


def remove_retweets(tweets, ignore_retweets_with_no_handle=False):

    pattern = '^RT @?[\w]+: '
    if ignore_retweets_with_no_handle:
        pattern.replace('@?', '@')

    without_retweets = tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(lambda t: re.sub(pattern, '', t))
    return without_retweets


def remove_urls(tweets):
    pattern = 'http\S+'

    without_urls = tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(lambda t: re.sub(pattern, '', t))
    return without_urls


def _get_ngram_counts(s, n, chars, ngram_counts, ignore_case):
    if ignore_case:
        s = s.lower()
    i = 0
    while i <= len(s) - n:
        for j in reversed(range(0, n)):
            if s[i+j] not in chars:
                i += j+1
                break
        else:
            ngram_counts[s[i:i+n]] += 1
            i += 1


def get_ngram_counts(tweets, n, chars, ignore_case):
    ngram_counts = defaultdict(lambda: 0)
    tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(_get_ngram_counts, args=(n, chars, ngram_counts, ignore_case))
    return ngram_counts


def calculate_significant_ngrams(df, n, chars, k, ignore_case=True):
    lang_ngram_counts = dict()

    for lang in LANGUAGES:
        tweets = df['tweet_text'][df['lang'] == lang]
        lang_ngram_counts[lang] = get_ngram_counts(tweets, n, chars, ignore_case)

    ngram_counts = pd.DataFrame.from_dict(lang_ngram_counts, orient='index')
    ngram_counts.fillna(0, inplace=True)
    selector = SelectKBest(chi2, k).fit(ngram_counts, range(len(ngram_counts)))
    best_k_cols = selector.get_support()

    return ngram_counts.columns[best_k_cols]


def _extract_char_ngrams(s, ngram_mapping, n, ignore_case):

    if ignore_case:
        s = s.lower()

    ngram_counts = np.zeros(len(ngram_mapping))

    for i in range(len(s)-n+1):
        try:
            ngram_index = ngram_mapping[s[i:i+n]]
            ngram_counts[ngram_index] += 1
        except KeyError:
            pass

    total_count = ngram_counts.sum()
    ngram_proportions = ngram_counts / total_count if total_count > 0 else ngram_counts

    return pd.Series(ngram_proportions)


def extract_char_ngrams(tweets, ngrams, ignore_case):
    ngram_mapping = OrderedDict(zip(ngrams, range(len(ngrams))))
    n = len(next(iter(ngram_mapping)))
    ngram_proportions = tweets.swifter.allow_dask_on_strings(enable=True).progress_bar(False).apply(_extract_char_ngrams, args=(ngram_mapping, n, ignore_case))

    ngram_proportions.columns = list(ngram_mapping)

    return ngram_proportions


def extract_ngrams_feature(df, n, chars, k, ignore_case=True):
    significant_ngrams = calculate_significant_ngrams(df, n, chars, k, ignore_case)
    ngram_proportions = extract_char_ngrams(df['tweet_text'], significant_ngrams, ignore_case)
    return ngram_proportions
