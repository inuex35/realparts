"""The walk, written where the foot goes rather than where the joints point.

The foot is flat along the ground while it carries and an arc while it does
not; the two joint angles follow by inverse kinematics. ``knee`` is the shin's
angle from vertical, not from the thigh: a mate turns about the target's axis.
"""
import math

#: the leg, in the document's own numbers. ``SOLE`` is the rounded end of the
#: bar below the ankle bore -- the part that actually touches the floor.
FEMUR, TIBIA, SOLE = 88.0, 104.0, 13.4

#: how far the foot travels while it is down, how far below the hip it rides,
#: and how high it is picked up. The ride height is a crouch: a leg standing at
#: its full 205 mm has nowhere left to reach, and the stride ends would be
#: unreachable rather than merely ugly.
STRIDE, HEIGHT, LIFT = 96.0, 194.0, 30.0

#: a trot: diagonal pairs together, half a cycle apart. Two feet down at every
#: instant, and always opposite corners, so it needs no balancing.
PHASE = {"fl": 0.0, "br": 0.0, "fr": 0.5, "bl": 0.5}

#: which way each knee folds. The front pair bend back and the rear pair bend
#: forward, because that is the one thing a two-link leg can say about which
#: end of the animal it is on.
BEND = {"fl": -1, "fr": -1, "bl": 1, "br": 1}

#: which half of the cycle the foot spends on the ground. A trot is an even
#: gait, so it is half.
DUTY = 0.5


def where(phase: float) -> tuple:
    """The foot's place under its own hip: forward of it, and below it.

    ``forward`` is along the way the robot walks, which is the way its head
    points; ``drop`` is the distance down. During stance the foot is still with
    respect to the *ground*, which means it slides backwards under the hip at
    an even rate -- that is what carries the body. During swing it lifts, goes
    back to the front, and sets down, on one arc of a sine so it leaves and
    lands with no sideways speed to scuff with.
    """
    turn = phase % 1.0
    if turn < DUTY:
        return STRIDE * (0.5 - turn / DUTY), HEIGHT
    lift = (turn - DUTY) / (1.0 - DUTY)
    return STRIDE * (lift - 0.5), HEIGHT - LIFT * math.sin(math.pi * lift)


def joints(forward: float, drop: float, bend: int = 1) -> tuple:
    """The hip and knee angles that put the foot there -- both from vertical.

    Two links and a known end point is a triangle with all three sides known,
    so the hip comes from the law of cosines and the knee from what is left
    over. That triangle has two roots, and they are the same foot with the
    joint folded the opposite way: ``bend`` picks one. It is a real choice
    rather than a detail, because it is most of what tells a front leg from a
    back one -- a dog's elbow points behind it and its stifle points in front.
    """
    reach = drop - SOLE
    span = math.hypot(forward, reach)
    # a foot asked for further away than the leg is long is a bug in the path,
    # not something to solve: clamp so it straightens rather than throwing, and
    # the caller's own check sees a foot that is not where it asked
    span = min(span, FEMUR + TIBIA - 1e-9)
    corner = (span * span + FEMUR * FEMUR - TIBIA * TIBIA) / (2.0 * FEMUR * span)
    hip = (math.atan2(forward, reach)
           + bend * math.acos(max(-1.0, min(1.0, corner))))
    knee = math.atan2(forward - FEMUR * math.sin(hip),
                      reach - FEMUR * math.cos(hip))
    return math.degrees(hip), math.degrees(knee)


def foot_of(hip: float, knee: float) -> tuple:
    """The other direction, for checking: where those two angles put the foot.

    This is the kernel's own arrangement written out, and it is here so a test
    can close the loop rather than trusting the arithmetic above.
    """
    hip, knee = math.radians(hip), math.radians(knee)
    return (FEMUR * math.sin(hip) + TIBIA * math.sin(knee),
            FEMUR * math.cos(hip) + TIBIA * math.cos(knee) + SOLE)


def pose(t: float) -> dict:
    """Every joint of every leg at this point in the cycle."""
    angles = {}
    for leg, offset in PHASE.items():
        hip, knee = joints(*where(t + offset), bend=BEND[leg])
        angles["hip_" + leg] = hip
        angles["knee_" + leg] = knee
    return angles
