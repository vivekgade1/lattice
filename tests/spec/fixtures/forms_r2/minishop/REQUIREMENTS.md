# Mini shop

## Orders

- A buyer can cancel their own pending order, and cancelling it again is refused with 409.
- A buyer can add a line to a pending order and the stored line has the requested quantity.
- An order's total is the sum of its lines.
- Stock is never negative.
- An order moves from pending to paid or cancelled, and from paid to shipped.

## Round 2

- A line's quantity is at least 1.
- A line's quantity is at most 100.
- A buyer reads and cancels only their own orders.
- A buyer can cancel an order only while it is pending.
- An order has at most 5 lines.
- Adding a line with a quantity of zero or less changes nothing.
- A product's stock is never negative, by itself.
