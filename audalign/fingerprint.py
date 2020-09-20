import numpy as np
import matplotlib.mlab as mlab
import matplotlib.pyplot as plt
from scipy.ndimage.filters import maximum_filter
from scipy.ndimage.morphology import (
    generate_binary_structure,
    iterate_structure,
    binary_erosion,
)
import hashlib
from operator import itemgetter

np.seterr(divide="ignore")

IDX_FREQ_I = 0
IDX_TIME_J = 1

######################################################################
# Sampling rate, related to the Nyquist conditions, which affects
# the range frequencies we can detect.
DEFAULT_FS = 44100

######################################################################
# Size of the FFT window, affects frequency granularity
DEFAULT_WINDOW_SIZE = 4096

######################################################################
# Ratio by which each sequential window overlaps the last and the
# next window. Higher overlap will allow a higher granularity of offset
# matching, but potentially more fingerprints.
DEFAULT_OVERLAP_RATIO = 0.5

######################################################################
# Degree to which a fingerprint can be paired with its neighbors --
# higher will cause more fingerprints, but potentially better accuracy.
DEFAULT_FAN_VALUE = 15

######################################################################
# Minimum amplitude in spectrogram in order to be considered a peak.
# This can be raised to reduce number of fingerprints, but can negatively
# affect accuracy.
# 50 roughly cuts number of fingerprints in half compared to 0
DEFAULT_AMP_MIN = 65

######################################################################
# Number of cells around an amplitude peak in the spectrogram in order
# for audalign to consider it a spectral peak. Higher values mean less
# fingerprints and faster matching, but can potentially affect accuracy.
PEAK_NEIGHBORHOOD_SIZE = 20

######################################################################
# Thresholds on how close or far fingerprints can be in time in order
# to be paired as a fingerprint. If your max is too low, higher values of
# DEFAULT_FAN_VALUE may not perform as expected.
MIN_HASH_TIME_DELTA = 10
MAX_HASH_TIME_DELTA = 200

######################################################################
# If True, will sort peaks temporally for fingerprinting;
# not sorting will cut down number of fingerprints, but potentially
# affect performance.
PEAK_SORT = True

######################################################################
# Number of bits to grab from the front of the SHA1 hash in the
# fingerprint calculation. The more you grab, the more memory storage,
# with potentially lesser collisions of matches.
FINGERPRINT_REDUCTION = 20


def fingerprint(
    channel_samples,
    fs=DEFAULT_FS,
    wsize=DEFAULT_WINDOW_SIZE,
    wratio=DEFAULT_OVERLAP_RATIO,
    fan_value=DEFAULT_FAN_VALUE,
    amp_min=DEFAULT_AMP_MIN,
    min_hash_time_delta=MIN_HASH_TIME_DELTA,
    max_hash_time_delta=MAX_HASH_TIME_DELTA,
    peak_sort=PEAK_SORT,
    plot=False,
):
    """
    FFT the channel, log transform output, find local maxima, then return
    locally sensitive hashes.

    Parameters
    ----------
    channel_samples : array[int]
        audio file data
    fs : int
        Sample Rate


    Returns
    -------
    hashes : dict{str: [int]}
        hashes of the form dict{hash: location}
    """
    # FFT the signal and extract frequency components
    arr2D = mlab.specgram(
        channel_samples,
        NFFT=wsize,
        Fs=fs,
        window=mlab.window_hanning,
        noverlap=int(wsize * wratio),
    )[0]

    # apply log transform since specgram() returns linear array
    arr2D = 10 * np.log2(arr2D)
    arr2D[arr2D == -np.inf] = 0  # replace infs with zeros

    # find local maxima
    local_maxima = get_2D_peaks(arr2D, plot=plot, amp_min=amp_min)

    # return hashes
    return generate_hashes(local_maxima, fan_value, min_hash_time_delta, max_hash_time_delta, peak_sort)


def get_2D_peaks(arr2D, plot=False, amp_min=DEFAULT_AMP_MIN):
    #  http://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.iterate_structure.html#scipy.ndimage.iterate_structure
    struct = generate_binary_structure(2, 1)
    neighborhood = iterate_structure(struct, PEAK_NEIGHBORHOOD_SIZE)

    # find local maxima using our filter shape
    local_max = maximum_filter(arr2D, footprint=neighborhood) == arr2D
    background = arr2D == 0
    eroded_background = binary_erosion(
        background, structure=neighborhood, border_value=1
    )

    # Boolean mask of arr2D with True at peaks (Fixed deprecated boolean operator by changing '-' to '^')
    detected_peaks = local_max ^ eroded_background

    # extract peaks
    amps = arr2D[detected_peaks]
    j, i = np.where(detected_peaks)

    # filter peaks
    amps = amps.flatten()
    peaks = zip(i, j, amps)
    peaks_filtered = filter(lambda x: x[2] > amp_min, peaks)  # freq, time, amp
    # get indices for frequency and time
    frequency_idx = []
    time_idx = []
    for x in peaks_filtered:
        frequency_idx.append(x[1])
        time_idx.append(x[0])

    if plot:
        # scatter of the peaks
        fig, ax = plt.subplots()
        ax.imshow(arr2D)
        ax.scatter(time_idx, frequency_idx, color="r")
        ax.set_xlabel("Time")
        ax.set_ylabel("Frequency")
        ax.set_title("Spectrogram")
        plt.gca().invert_yaxis()
        plt.show()

    return zip(frequency_idx, time_idx)


def generate_hashes(peaks, fan_value, min_hash_time_delta, max_hash_time_delta, peak_sort):
    """
    Hash list structure:
       sha1_hash[0:30]    time_offset
    [(e05b341a9b77a51fd26..., 32), ... ]
    """
    hash_dict = {}
    peaks = list(peaks)
    if peak_sort:
        peaks = sorted(peaks, key=lambda x: x[1])
    # print("Length of Peaks List is: {}".format(len(peaks)))

    for i in range(0, len(peaks), 1):
        freq1 = peaks[i][IDX_FREQ_I]
        t1 = peaks[i][IDX_TIME_J]
        for j in range(1, fan_value - 1):
            if i + j < len(peaks):
                freq2 = peaks[i + j][IDX_FREQ_I]
                t2 = peaks[i + j][IDX_TIME_J]
                for k in range(j + 1, fan_value):
                    if (i + k) < len(peaks):

                        freq3 = peaks[i + k][IDX_FREQ_I]
                        t3 = peaks[i + k][IDX_TIME_J]

                        t_delta = t3 - t1

                        if (
                            t_delta >= min_hash_time_delta
                            and t_delta <= max_hash_time_delta
                        ):

                            t_delta = t2 - t1

                            if (
                                t_delta >= min_hash_time_delta
                                and t_delta <= max_hash_time_delta
                            ):
                                h = hashlib.sha1(
                                    # f"{freq1}|{freq2}|{t_delta}".encode(
                                    # f"{freq1-freq2}|{freq2-freq3}|{freq1//400}|{freq3//400}|{(t2-t1)/(t3-t1):.8f}".encode(
                                    f"{freq1-freq2}|{freq2-freq3}|{(t2-t1)/(t3-t1):.8f}".encode(
                                        "utf-8"
                                    )
                                ).hexdigest()[0:FINGERPRINT_REDUCTION]
                                if h not in hash_dict:
                                    hash_dict[h] = [int(t1)]
                                else:
                                    hash_dict[h] += [int(t1)]

    return hash_dict
