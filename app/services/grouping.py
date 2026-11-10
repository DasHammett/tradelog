"""
Fill grouping
=============
Partial fills of one order arrive as separate executions (e.g. a 50-share SELL
filled as 10 @ DARK + 40 @ MEMX). Counting each fill as its own trade inflates
the trade count and splits one win/loss into several.

Raw rows in stg_executions are never changed. This module groups them at
compute time, per (date, symbol):

  * Executions that carry an IBKR order ID (ibOrderID) are grouped by
    (side, order ID) — this also merges partial fills that complete in
    different seconds.
  * Executions without an order ID fall back to (side, time), i.e. fills of
    the same side in the same second (Flex times have 1-second resolution).

A FillGroup behaves like a single execution (same attribute names the
templates and matching code already use), with summed quantity / fees and a
quantity-weighted average price.
"""
from collections import OrderedDict

POSITION_EPS = 0.0001

# Treat these as "no order ID" (Flex sends 0 / empty when it has none)
_EMPTY_ORDER_IDS = {"", "0", "none", "null"}


def clean_order_id(value):
    """Normalise an order ID coming from the XML or DB. Returns str or None."""
    if value is None:
        return None
    v = str(value).strip()
    return None if v.lower() in _EMPTY_ORDER_IDS else v


class FillGroup:
    """One or more executions merged into a single logical fill."""

    def __init__(self, members):
        # members: StgExecution rows, already in (time, exec_id) order
        self.members = list(members)
        first = self.members[0]
        self.side = first.side
        self.time = first.time                      # first fill time
        self.exec_id = first.exec_id                # representative id (first fill)
        self.exec_ids = [m.exec_id for m in self.members]
        self.ib_order_id = clean_order_id(getattr(first, "ib_order_id", None))
        self.order_type = first.order_type
        self.open_close = first.open_close

        self.quantity = sum(m.quantity for m in self.members)
        notional = sum(m.quantity * m.price for m in self.members)
        self.price = notional / self.quantity if self.quantity else first.price

        self.commission         = sum(m.commission or 0.0         for m in self.members)
        self.broker_charge      = sum(m.broker_charge or 0.0      for m in self.members)
        self.third_party_charge = sum(m.third_party_charge or 0.0 for m in self.members)
        self.clearing_charge    = sum(m.clearing_charge or 0.0    for m in self.members)
        self.regulatory_charge  = sum(m.regulatory_charge or 0.0  for m in self.members)

        exchanges = []
        for m in self.members:
            if m.exchange and m.exchange not in exchanges:
                exchanges.append(m.exchange)
        self.exchange = ", ".join(exchanges) if exchanges else None

        # Filled in by annotate_cycles()
        self.cycle = 0

    @property
    def fill_count(self):
        return len(self.members)

    @property
    def is_grouped(self):
        return len(self.members) > 1

    @property
    def last_time(self):
        return max(m.time for m in self.members)

    def __repr__(self):
        return f"<FillGroup {self.side} {self.quantity}@{self.price:.4f} x{len(self.members)}>"


def group_executions(executions):
    """
    Group executions of ONE date+symbol into FillGroups.
    Returns groups ordered by (first fill time, BUY before SELL), so a
    same-second stop-loss never arrives before its BUY.
    """
    ordered = sorted(executions, key=lambda e: (e.time, e.exec_id))
    buckets = OrderedDict()
    for ex in ordered:
        oid = clean_order_id(getattr(ex, "ib_order_id", None))
        key = (ex.side, "order", oid) if oid else (ex.side, "time", ex.time)
        buckets.setdefault(key, []).append(ex)

    groups = [FillGroup(members) for members in buckets.values()]
    groups.sort(key=lambda g: (g.time, g.side))     # "BUY" < "SELL"
    return groups


def annotate_cycles(groups):
    """
    Number each group with its flat-to-flat position cycle: a cycle starts at
    the first BUY after the position is 0 and ends when it returns to 0.
    Orphan SELLs (no open position) keep cycle 0.
    """
    pos_qty = 0.0
    cycle = 0
    for g in groups:
        if g.side == "BUY":
            if pos_qty <= POSITION_EPS:
                cycle += 1
                pos_qty = 0.0
            pos_qty += g.quantity
            g.cycle = cycle
        else:
            if pos_qty > POSITION_EPS:
                g.cycle = cycle
                pos_qty -= min(g.quantity, pos_qty)
                if pos_qty <= POSITION_EPS:
                    pos_qty = 0.0
            else:
                g.cycle = 0
    return groups
