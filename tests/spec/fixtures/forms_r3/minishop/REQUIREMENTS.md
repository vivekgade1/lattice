# Mini shop

## Orders

- A buyer can cancel their own pending order, and cancelling it again is refused with 409.
- A buyer can add a line to a pending order and the stored line has the requested quantity.
- An order's total is the sum of its lines.
- Stock is never negative.
- An order moves from pending to paid or cancelled, and from paid to shipped.

## Round 3

- An order is addressed by a positive number.
- A list of orders is requested with a positive limit.
- A buyer cancels only their own orders, and someone else's is refused.
- An order has at most 3 lines.
- A product appears in at most 2 lines.
