# Mini shop

## Orders

- A buyer can cancel their own pending order, and cancelling it a second time is refused with 409.
- A buyer can add a line to a pending order and the stored line has the requested quantity.
- An order's total is the sum of its lines.
- Stock is never negative.
- An order moves from pending to paid or cancelled, and from paid to shipped.
