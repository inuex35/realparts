"""Draw the stress colour bar onto a rendered field image.

    python3 tools/render/legend.py build/stress.png 17.5 50.1 "mount_load"

Kept out of the Blender process on purpose: it is image work, not scene work,
and the ramp it draws is the one in ``blender_addon/link/sync.py``.

Needs OpenCV (``pip install opencv-python-headless``), which the kernel does
not: this is a development script, not part of the tool.
"""
import sys

import cv2

RAMP = [(0.00, (0.02, 0.10, 0.55)), (0.35, (0.00, 0.65, 0.75)),
        (0.60, (0.15, 0.80, 0.20)), (0.80, (0.95, 0.80, 0.10)), (1.00, (0.85, 0.05, 0.05))]


def colour(t):
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in zip(RAMP, RAMP[1:]):
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return [c0[i] + (c1[i] - c0[i]) * k for i in range(3)]
    return list(RAMP[-1][1])


def draw(path, scale, peak, title):
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    bar_h, bar_w = int(h * 0.52), 26
    x0, y0 = w - 150, int(h * 0.24)
    for j in range(bar_h):
        t = 1.0 - j / (bar_h - 1)
        # sRGB, to match the Standard view transform the render used
        bgr = [int(255 * (c ** (1 / 2.2))) for c in colour(t)][::-1]
        img[y0 + j, x0:x0 + bar_w] = bgr
    cv2.rectangle(img, (x0 - 1, y0 - 1), (x0 + bar_w, y0 + bar_h), (210, 210, 210), 1)

    font, fs = cv2.FONT_HERSHEY_DUPLEX, 0.45
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = int(y0 + (1 - frac) * (bar_h - 1))
        cv2.line(img, (x0 + bar_w, y), (x0 + bar_w + 5, y), (210, 210, 210), 1)
        cv2.putText(img, "%.0f" % (frac * scale), (x0 + bar_w + 9, y + 4), font, fs,
                    (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(img, "von Mises (MPa)", (x0 - 34, y0 - 14), font, fs, (235, 235, 235), 1,
                cv2.LINE_AA)
    cv2.putText(img, "peak %.0f MPa" % peak, (x0 - 34, y0 + bar_h + 26), font, fs,
                (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(img, title, (28, 40), font, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.imwrite(path, img)
    print("LEGEND wrote", path)


if __name__ == "__main__":
    draw(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]),
         sys.argv[4] if len(sys.argv) > 4 else "")
