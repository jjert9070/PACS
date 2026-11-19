# Fixed ROI comparison: Gaussian

Generated synthetic 256x256 RGB image; no patient or external image data.
Coordinates are original pixels; right/bottom exclusive. All conditions use the same regions.
Brightness uses Pillow L; population variance uses NumPy float64 and ddof=0.

| Region | Bounds | Size | Pixels |
|---|---|---|---|
| flat | (16, 64, 48, 192) | 32x128 | 4096 |
| edge | (112, 64, 144, 192) | 32x128 | 4096 |
| thin_line | (52, 64, 70, 192) | 18x128 | 2304 |

## Noise sigma=20 (full conditions are in metrics.csv)

| Region | Filter sigma | Input mean | Input variance | Result mean | Result variance | L RMSE to clean |
|---|---|---|---|---|---|---|
| flat | 0 | 80.417969 | 179.839462 | 80.417969 | 179.839462 | 13.416936 |
| edge | 0 | 128.006348 | 2465.336385 | 128.006348 | 2465.336385 | 13.149698 |
| thin_line | 0 | 95.848524 | 2201.536517 | 95.848524 | 2201.536517 | 13.269787 |
| flat | 1 | 80.417969 | 179.839462 | 80.392578 | 13.380550 | 3.678949 |
| edge | 1 | 128.006348 | 2465.336385 | 128.028320 | 2152.793632 | 8.301773 |
| thin_line | 1 | 95.848524 | 2201.536517 | 95.830729 | 899.814229 | 22.982085 |
| flat | 2 | 80.417969 | 179.839462 | 80.378174 | 3.327444 | 1.862917 |
| edge | 2 | 128.006348 | 2465.336385 | 128.073486 | 1979.150606 | 11.494213 |
| thin_line | 2 | 95.848524 | 2201.536517 | 95.803385 | 374.958305 | 34.631348 |
| flat | 4 | 80.417969 | 179.839462 | 80.335449 | 0.919212 | 1.015745 |
| edge | 4 | 128.006348 | 2465.336385 | 128.117188 | 1658.136169 | 16.335637 |
| thin_line | 4 | 95.848524 | 2201.536517 | 95.404514 | 78.894528 | 41.387723 |

## Interpretation and limits

Flat-region variance describes noise fluctuations. Edge/line-region variance also includes genuine structure.
A lower variance alone does not show better quality: stronger smoothing may remove edges and thin lines.
RMSE compares the same L region with the clean synthetic reference, not with the noisy input.
RGB noise is converted to L before statistics; noise sigma is not the resulting L standard deviation.
Sigma=0 preserves every input pixel, so input and result statistics are equal.
The montage enlarges ROI crops for visibility; calculation uses the original crops without resizing.
The montage shows RGB crops; numeric metrics consistently use Pillow L brightness.
These synthetic results are not Lenna quality metrics, diagnostic evidence or a universal best sigma.
Web rotation/flip and Firebase persistence are separate integration checks, not demonstrated by this experiment.
