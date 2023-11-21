import argparse
import os
from pathlib import Path
import time
import sys
import string

import numpy as np
from scipy.io import loadmat
from scipy.ndimage import gaussian_filter1d
from sklearn.decomposition import PCA
from sklearn.manifold import TSNE
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier
from matplotlib import pyplot as plt
from matplotlib import colormaps


MATPLOTLIB_COLORS = plt.rcParams["axes.prop_cycle"].by_key()["color"]

ALL_CHARS = [
    "doNothing",
    *string.ascii_lowercase,
    "greaterThan",
    "tilde",
    "questionMark",
    "apostrophe",
    "comma",
]
CHAR_REPLACEMENTS = {
    "doNothing": "rest",
    "greaterThan": ">",
    "tilde": "~",
    "questionMark": "?",
    "apostrophe": "'",
    "comma": ",",
}
CHAR_TO_CLASS_MAP = {char: idx for idx, char in enumerate(ALL_CHARS)}
CLASS_TO_CHAR_MAP = {idx: char for idx, char in enumerate(ALL_CHARS)}

PRE_GO_CUE_BINS = 50
POST_GO_CUE_BINS = 120

OUTPUTS_DIR = os.path.abspath("./outputs")


########################################################################################
# Main function.
########################################################################################


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--save_plots", action="store_true")
    args = parser.parse_args()
    save_plots = args.save_plots

    ## Load the data.

    data_dicts = load_data()

    ## Preprocess and label the data.

    (
        trial_neural_activities,
        trial_labels,
        trial_session_idxs,
        trial_block_idxs,
        session_pca_models,
    ) = prepare_data(data_dicts)

    ## Plot the PCs activity across trials, grouped by character to see patterns.

    plot_PCs(
        trial_neural_activities,
        trial_labels,
        trial_session_idxs,
        trial_block_idxs,
        session_pca_models,
        save_plots,
    )

    ## Plot the trials projected into t-SNE space, to see if they're clustering well.

    plot_tSNE(
        trial_neural_activities,
        trial_labels,
        trial_session_idxs,
        trial_block_idxs,
        session_pca_models,
        save_plots,
    )


########################################################################################
# Helper functions.
########################################################################################


def load_data():
    """
    Scrape the data directory and load the data files from disk into dicts in memory.
    """

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
        # break  # for testing quickly

    return data_dicts


def prepare_data(data_dicts):
    """
    Take the dicts of session data, z-score the neural data, slice up trials to get
    inputs and labels.
    """

    print("Preparing data ...")

    trial_neural_activities = []
    trial_labels = []
    trial_session_idxs = []
    trial_block_idxs = []
    session_pca_models = []
    for session_idx, data_dict in enumerate(data_dicts):
        neural_activity = data_dict["neuralActivityTimeSeries"]
        go_cue_bins = data_dict["goPeriodOnsetTimeBin"].ravel().astype(int)
        prompts = [a[0] for a in data_dict["characterCues"].ravel()]
        block_by_bin = data_dict["blockNumsTimeSeries"].ravel()
        block_nums = data_dict["blockList"].ravel()
        session_block_means = data_dict["meansPerBlock"]
        session_stddevs = data_dict["stdAcrossAllData"]

        # Z-score each block's data based on that block's mean and stddev.
        zscored_neural_activity = np.zeros_like(neural_activity, dtype=np.float32)
        for block_idx, block_num in enumerate(block_nums):
            block_neural_activity = neural_activity[block_by_bin == block_num]
            block_means = session_block_means[block_idx]
            with np.errstate(divide="ignore", invalid="ignore"):
                zscored_block_neural_activity = (
                    block_neural_activity - block_means
                ) / session_stddevs
                zscored_block_neural_activity = np.nan_to_num(
                    zscored_block_neural_activity, nan=0, posinf=0, neginf=0
                )
            zscored_neural_activity[
                block_by_bin == block_num
            ] = zscored_block_neural_activity

        # Fit a PCA model (only on this session) with which to transform z-scored neural
        # data.
        session_pca_model = PCA()
        session_pca_model.fit(zscored_neural_activity)
        session_pca_models.append(session_pca_model)

        print(f"Creating labeled pairs for session {session_idx} ...")
        for trial_idx, go_cue_bin in enumerate(go_cue_bins):
            # Get the training window for this trial.
            window_start_bin = int(go_cue_bin) - PRE_GO_CUE_BINS
            window_end_bin = int(go_cue_bin) + POST_GO_CUE_BINS
            window_neural_activity = zscored_neural_activity[
                window_start_bin:window_end_bin
            ]

            label = prompts[trial_idx]

            trial_neural_activities.append(window_neural_activity)
            trial_labels.append(label)
            trial_session_idxs.append(session_idx)
            trial_block_idxs.append(f"{session_idx}_{block_by_bin[go_cue_bin]}")

    trial_neural_activities = np.array(trial_neural_activities)
    trial_labels = np.array(trial_labels)
    trial_session_idxs = np.array(trial_session_idxs)
    trial_block_idxs = np.array(trial_block_idxs)

    return (
        trial_neural_activities,
        trial_labels,
        trial_session_idxs,
        trial_block_idxs,
        session_pca_models,
    )


def plot_PCs(
    trial_neural_activities,
    trial_labels,
    trial_session_idxs,
    trial_block_idxs,
    session_pca_models,
    save_plots,
):
    """
    Plot principal component values across individual trials.
    """

    ## Separate out each session.

    all_session_idxs = sorted(set(trial_session_idxs))

    for session_idx in all_session_idxs[:1]:
        # Get just the trials for this session.
        session_trial_neural_activities = trial_neural_activities[
            trial_session_idxs == session_idx
        ]
        session_trial_labels = trial_labels[trial_session_idxs == session_idx]

        session_trial_PCs = np.array(
            [
                session_pca_models[session_idx].transform(neural_activities)
                for neural_activities in session_trial_neural_activities
            ]
        )
        session_trial_PCs_smoothed = np.array(
            [gaussian_filter1d(w, sigma=3.0, axis=0) for w in session_trial_PCs]
        )
        session_trial_PCs_smoothed_by_char = {
            char: np.array(
                [
                    w
                    for w, c in zip(session_trial_PCs_smoothed, session_trial_labels)
                    if c == char
                ]
            )
            for char in ALL_CHARS
        }

        NUM_PCS_TO_PLOT = 3

        for char in ALL_CHARS[:5]:
            if len(session_trial_PCs_smoothed_by_char[char]) == 0:
                continue

            fig, axs = plt.subplots(1, NUM_PCS_TO_PLOT)

            for pc_idx, ax in enumerate(axs):
                ax.imshow(
                    session_trial_PCs_smoothed_by_char[char][:, :, pc_idx],
                    cmap=colormaps["bwr"],
                )

                ax.axvline(PRE_GO_CUE_BINS, color="black")  # put a line at the go cue

                ax.set_xticks([0, 50, 100, 150])
                ax.set_xticklabels([-0.5, 0.0, 0.5, 1.0])
                ax.set_xlabel("time (s)")

                ax.set_ylabel("trial")

                ax.set_title(f"{char} (PC{pc_idx + 1})")

            fig.suptitle(f"Neural activity (PCs) during trials of '{char}'")

            fig.tight_layout()

            if save_plots:
                # Save the figure.
                Path(OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
                plot_filename = f"neural_activity_PCs_during_session_{session_idx}_{char}_trials.png"
                plot_filepath = os.path.join(OUTPUTS_DIR, plot_filename)
                plt.savefig(plot_filepath)
                plt.close()
            else:
                plt.show()


def plot_tSNE(
    trial_neural_activities,
    trial_labels,
    trial_session_idxs,
    trial_block_idxs,
    session_pca_models,
    save_plots,
):
    """Plot the t-SNE projections of the trials in 2D space."""

    ## Separate out each session.

    all_session_idxs = sorted(set(trial_session_idxs))[:1]

    num_plots = len(all_session_idxs)
    num_cols = int(np.ceil(np.sqrt(num_plots)))
    num_rows = int(np.ceil(num_plots / num_cols))

    fig, axs = plt.subplots(num_rows, num_cols)

    for session_idx in all_session_idxs:
        row_idx = session_idx // num_cols
        col_idx = session_idx % num_cols

        try:
            ax = axs[row_idx, col_idx]
        except:
            ax = axs

        # Get just the trials for this session.
        session_trial_neural_activities = trial_neural_activities[
            trial_session_idxs == session_idx
        ]
        session_trial_labels = trial_labels[trial_session_idxs == session_idx]

        # Transform the neural activity using our PCA model trained on all sessions.
        session_pca_model = session_pca_models[session_idx]
        session_trial_PCs = np.array(
            [session_pca_model.transform(w) for w in session_trial_neural_activities]
        )
        # Smooth the PCs over time.
        session_trial_PCs_smoothed = np.array(
            [gaussian_filter1d(w, sigma=3.0, axis=0) for w in session_trial_PCs]
        )
        # Take just the window starting at the go cue.
        session_trial_PCs_windowed = session_trial_PCs_smoothed[
            :, PRE_GO_CUE_BINS : PRE_GO_CUE_BINS + POST_GO_CUE_BINS
        ]
        # Flatten the PCs so tSNE can operate on 1D vectors.
        session_trial_PCs_flattened = np.reshape(
            session_trial_PCs_windowed, (session_trial_PCs_windowed.shape[0], -1)
        )

        ## The neural data is now transformed and separated by trial.
        ## Now fit a t-SNE model.

        PERPLEXITY = 7
        tsne_model = TSNE(perplexity=PERPLEXITY)
        trials_projected = tsne_model.fit_transform(session_trial_PCs_flattened)

        ## Plot the t-SNE-projected trials in 2D space, colored by character to see if
        ## they cluster well.

        for char_idx, char in enumerate(ALL_CHARS):
            char_trials_projected = trials_projected[session_trial_labels == char]
            if len(char_trials_projected) == 0:
                continue
            color_idx = char_idx % len(MATPLOTLIB_COLORS)
            color = MATPLOTLIB_COLORS[color_idx]
            ax.scatter(
                char_trials_projected[:, 0],
                char_trials_projected[:, 1],
                color=color,
                alpha=0.8,
                label=char,
            )
            # Label the cluster with the char text itself located at the mean point.
            char_mean = np.median(char_trials_projected, axis=0)
            ax.text(
                char_mean[0],
                char_mean[1],
                CHAR_REPLACEMENTS.get(char, char),
                color=color,
                fontsize=16,
                fontweight="bold",
            )

            ax.set_xlabel("t-SNE axis 0")
            ax.set_xticks([])

            ax.set_ylabel("t-SNE axis 1")
            ax.set_yticks([])

        ax.set_title(f"Session {session_idx}")

    # Turn off the unused subplots.
    for ax_idx in range(num_plots, num_rows * num_cols):
        row_idx = ax_idx // num_cols
        col_idx = ax_idx % num_cols
        ax = axs[row_idx, col_idx]
        ax.axis("off")

    fig.set_figwidth(20)
    fig.set_figheight(20)

    fig.tight_layout()

    if save_plots:
        # Save the figure.
        Path(OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
        plot_filename = f"neural_activity_tSNE_projections.png"
        plot_filepath = os.path.join(OUTPUTS_DIR, plot_filename)
        plt.savefig(plot_filepath)
        plt.close()
    else:
        plt.show()


if __name__ == "__main__":
    main()