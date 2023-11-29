import argparse
import os
from pathlib import Path
import time
import sys
import string
import itertools
import random
from collections import Counter

import numpy as np
from scipy.io import loadmat
from scipy.ndimage import gaussian_filter1d
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix
from matplotlib import pyplot as plt


MATPLOTLIB_COLORS = plt.rcParams["axes.prop_cycle"].by_key()["color"]

ALL_CHARS = [
    *string.ascii_lowercase,
    "greaterThan",
    "tilde",
    "questionMark",
    "apostrophe",
    "comma",
]
CHAR_REPLACEMENTS = {
    "greaterThan": ">",
    "tilde": "~",
    "questionMark": "?",
    "apostrophe": "'",
    "comma": ",",
}
CHAR_TO_CLASS_MAP = {char: idx for idx, char in enumerate(ALL_CHARS)}
CLASS_TO_CHAR_MAP = {idx: char for idx, char in enumerate(ALL_CHARS)}

REACTION_TIME_BINS = 10
TRAINING_WINDOW_BINS = 150

OUTPUTS_DIR = os.path.abspath("./outputs")


########################################################################################
# Main function.
########################################################################################


def main():
    ## Load the data.

    data_dicts = load_data()

    ## Preprocess and label the data.

    X_train, X_validation, X_test, y_train, y_validation, y_test = organize_data(
        data_dicts
    )

    ## Flatten each trial's neural data for decoders that operate on 1D vectors.

    flattened_X_train = np.reshape(X_train, (X_train.shape[0], -1))
    flattened_X_validation = np.reshape(X_validation, (X_validation.shape[0], -1))
    flattened_X_test = np.reshape(X_test, (X_test.shape[0], -1))

    ## Train a logistic regression model on the preprocessed training data.

    print("Training logistic regression model ...")

    logistic_regression_model = LogisticRegression(solver="newton-cg")
    logistic_regression_model.fit(flattened_X_train, y_train)

    ## Evaluate logistic regression model by calculating accuracy on the test set.

    y_pred_test = logistic_regression_model.predict(flattened_X_test)

    test_accuracy = np.sum(y_pred_test == y_test) / len(y_test)

    accuracy_str = f"{round(test_accuracy, 3):.3f}"
    print(f"accuracy: {accuracy_str}")

    ## Optionally plot the confusion matrix.

    show_confusion_matrix = False
    if show_confusion_matrix:
        fig, confusion_ax = plt.subplots()

        confusion_results = confusion_matrix(y_test, y_pred_test, normalize="true")

        heatmap = confusion_ax.imshow(confusion_results, origin="lower")

        fig.colorbar(heatmap, ax=confusion_ax)

        confusion_ax.set_xticks(np.arange(len(ALL_CHARS)))
        confusion_ax.set_xticklabels(ALL_CHARS, rotation=45, ha="right")
        confusion_ax.set_xlabel("predicted character")

        confusion_ax.set_yticks(np.arange(len(ALL_CHARS)))
        confusion_ax.set_yticklabels(ALL_CHARS)
        confusion_ax.set_ylabel("true character")

        model_str = "LogisticRegression"
        confusion_ax.set_title(
            f"{model_str} on single-letter instructed-delay task (accuracy: {accuracy_str})"
        )

        plt.tight_layout()

        plt.show()


def load_data():
    """"""
    DATA_DIR = os.path.abspath("./handwritingBCIData/Datasets/")
    letters_filepaths = []
    for root, _, filenames in os.walk(DATA_DIR):
        for filename in filenames:
            filepath = os.path.join(root, filename)
            if filename == "singleLetters.mat":
                letters_filepaths.append(filepath)

    letters_filepaths = sorted(letters_filepaths)

    data_dicts = []
    for filepath in letters_filepaths:
        print(f"Loading {filepath} ...")
        data_dict = loadmat(filepath)
        data_dicts.append(data_dict)

    return data_dicts


def organize_data(data_dicts):
    """"""

    print("Preparing data ...")

    X_train = []
    X_validation = []
    X_test = []
    y_train = []
    y_validation = []
    y_test = []

    validation_count_by_char = Counter()
    test_count_by_char = Counter()

    # Iterate through the sessions.
    # NUM_SESSIONS = 1
    NUM_SESSIONS = None
    for data_dict in data_dicts[:NUM_SESSIONS]:
        neural = data_dict["neuralActivityTimeSeries"]
        go_cue_bins = data_dict["goPeriodOnsetTimeBin"].ravel().astype(int)
        delay_cue_bins = data_dict["delayPeriodOnsetTimeBin"].ravel().astype(int)
        prompts = np.array([a[0] for a in data_dict["characterCues"].ravel()])
        block_by_bin = data_dict["blockNumsTimeSeries"].ravel()
        block_nums = data_dict["blockList"].ravel()

        # Iterate through each block in this session.
        for block_num in block_nums:
            # Get means and stddevs from a random set of train trials in the block, and
            # the rest of the trials can be used for validation and test.
            block_trial_mask = [block_by_bin[b] == block_num for b in go_cue_bins]
            num_trials_in_block = sum(block_trial_mask)
            random_trial_idxs = list(range(num_trials_in_block))
            random.shuffle(random_trial_idxs)
            train_end_idx = int(num_trials_in_block * 0.6)
            train_trial_idxs = random_trial_idxs[:train_end_idx]
            block_go_cue_bins = go_cue_bins[block_trial_mask]
            block_delay_cue_bins = delay_cue_bins[block_trial_mask]
            block_prompts = prompts[block_trial_mask]
            # Loop through the train trials and add the neural data to our list.
            neural_to_zscore_based_on = []
            for trial_idx in train_trial_idxs:
                # For convenience, ignore the last trial in the block.
                if trial_idx + 1 >= len(block_delay_cue_bins):
                    continue
                start_bin = block_delay_cue_bins[trial_idx]
                end_bin = block_delay_cue_bins[trial_idx + 1]
                neural_to_zscore_based_on.extend(neural[start_bin:end_bin])
            neural_to_zscore_based_on = np.array(neural_to_zscore_based_on)
            block_means = np.mean(neural_to_zscore_based_on, axis=0)
            block_stddevs = np.std(neural_to_zscore_based_on, axis=0)

            print(f"Creating labeled pairs for block {block_num} ...")
            for trial_idx in range(num_trials_in_block):
                # Get the training window for this trial.
                go_cue_bin = block_go_cue_bins[trial_idx]
                window_start_bin = int(go_cue_bin) + REACTION_TIME_BINS
                window_end_bin = window_start_bin + TRAINING_WINDOW_BINS
                # Get the neural data in this window.
                trial_neural = neural[window_start_bin:window_end_bin]
                # Z-score the neural data using the block-specific means and stddevs.
                with np.errstate(divide="ignore", invalid="ignore"):
                    trial_zscored_neural = (trial_neural - block_means) / block_stddevs
                trial_zscored_neural = np.nan_to_num(
                    trial_zscored_neural, nan=0, posinf=0, neginf=0
                )

                # Get the character for this trial.
                trial_label = block_prompts[trial_idx]

                # Skip rest trials.
                if trial_label == "doNothing":
                    continue

                # Add the trial to the appropriate set of data (train, validation, or
                # test).
                if trial_idx in train_trial_idxs:
                    X_train.append(trial_zscored_neural)
                    y_train.append(trial_label)
                else:
                    # Put the trial into either validation or test, whichever has fewer
                    # of this trial's character so far.
                    if (
                        validation_count_by_char[trial_label]
                        < test_count_by_char[trial_label]
                    ):
                        X_validation.append(trial_zscored_neural)
                        y_validation.append(trial_label)
                        validation_count_by_char[trial_label] += 1
                    else:
                        X_test.append(trial_zscored_neural)
                        y_test.append(trial_label)
                        test_count_by_char[trial_label] += 1

    # Smooth the neural data over time.
    SMOOTHING_STDDEV = 3.0
    X_train = np.array(
        [gaussian_filter1d(w, sigma=SMOOTHING_STDDEV, axis=0) for w in X_train]
    )
    X_validation = np.array(
        [gaussian_filter1d(w, sigma=SMOOTHING_STDDEV, axis=0) for w in X_validation]
    )
    X_test = np.array(
        [gaussian_filter1d(w, sigma=SMOOTHING_STDDEV, axis=0) for w in X_test]
    )

    # Convert the characters to ints, for compatibility with pytorch.
    y_train = np.array([CHAR_TO_CLASS_MAP[ch] for ch in y_train])
    y_validation = np.array([CHAR_TO_CLASS_MAP[ch] for ch in y_validation])
    y_test = np.array([CHAR_TO_CLASS_MAP[ch] for ch in y_test])

    print(f"X_train.shape: {X_train.shape}")
    print(f"X_validation.shape: {X_validation.shape}")
    print(f"X_test.shape: {X_test.shape}")
    print(f"y_train.shape: {y_train.shape}")
    print(f"y_validation.shape: {y_validation.shape}")
    print(f"y_test.shape: {y_test.shape}")

    return X_train, X_validation, X_test, y_train, y_validation, y_test


if __name__ == "__main__":
    main()