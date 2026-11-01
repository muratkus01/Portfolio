"""The pure parts of the aFRR capacity model: no I/O and no solver.

The optimisers, the safety layer and the settlement read what is defined here, so that none
of them imports `afrr_data`, which reads regelleistung.net's files.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# What a reservation can be paid on (`paid_prices`): B2 on any of them, as a sensitivity;
# B3, which bids, always on "bid".
PRICE_BASES = ("bid", "average", "marginal")


@dataclass(frozen=True)
class CapacityPrices:
    """The window's aFRR capacity products, B3's gates and bids, and the realised prices.

    Every field holds one value per 15-minute step of the window, as
    `afrr_data.capacity_prices` maps them. Prices are in EUR/MW/h, "up" for positive reserve
    (the POS products) and "down" for negative reserve (the NEG products).
    block: the step's product, a running id per Berlin date and 4-hour slice, increasing in
        time and shared by both directions, e.g. [0, 0, ..., 1, ...].
    day: the step's Berlin day, 0 for the window's first.
    gate, award: True at 09:00 and at 09:30 Berlin on a day whose next day lies in the
        window. B3 bids for all of the next day at the gate and learns at the award which
        bids won (MfRRA § 20 (3)).
    average_up, average_down: the realised German volume-weighted average of the product.
    marginal_up, marginal_down: the realised German marginal, the highest accepted bid.
    bid_up, bid_down: B3's bid, the same product's German average on the previous Berlin
        day, to whole cents; NaN on the window's first day, which has no gate in the window.
    """
    block: np.ndarray
    day: np.ndarray
    gate: np.ndarray
    award: np.ndarray
    average_up: np.ndarray
    average_down: np.ndarray
    marginal_up: np.ndarray
    marginal_down: np.ndarray
    bid_up: np.ndarray
    bid_down: np.ndarray


def paid_prices(prices: CapacityPrices, basis: str) -> tuple[np.ndarray, np.ndarray]:
    """What a reservation is paid per step, EUR/MW/h, up and down.

    The auction pays as bid (BK6-15-158, Tenor 9a) and accepts bids from the lowest price up
    (MfRRA § 20 (8)), so a bid is accepted where it is at or below the realised marginal, a
    tie included. The battery's few MW are taken not to move the marginal.
    prices: the window's CapacityPrices.
    basis: "bid", the bid where it was accepted, and 0 where it was not or where there is no
        bid (NaN, on the window's first day); or, as B2's sensitivities, "average", what the
        average accepted MW earned, or "marginal", the most any bid could have earned.
    Returns (up, down), one price per step.
    Raises ValueError for any other basis.
    """
    if basis == "bid":
        # NaN <= x is False, so a step without a bid is paid nothing
        return (np.where(prices.bid_up <= prices.marginal_up, prices.bid_up, 0.0),
                np.where(prices.bid_down <= prices.marginal_down, prices.bid_down, 0.0))
    if basis == "average":
        return prices.average_up, prices.average_down
    if basis == "marginal":
        return prices.marginal_up, prices.marginal_down
    raise ValueError(f"unknown aFRR price basis {basis!r}; it is one of {PRICE_BASES}")
