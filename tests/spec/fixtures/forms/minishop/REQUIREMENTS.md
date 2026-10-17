# Mini shop

## Orders

- A buyer can cancel their own pending order, and cancelling it again is refused with 409.
- A buyer can add a line to a pending order and the stored line has the requested quantity.
- An order's total is the sum of its lines.
- Stock is never negative.
- An order moves from pending to paid or cancelled, and from paid to shipped.

## Typed conditions

- A line's quantity is at least 1.
- A line's quantity is at most 100.
- An order's status is one of pending, paid, shipped or cancelled.
- A buyer reads, cancels and adds lines only on their own orders.
- An order has at most 5 lines.
