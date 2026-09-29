# Lattice import of `bench.import_repos.multivendor.app`

- LLM loop: on, method M2, model claude-sonnet-5
- routes: 17; verified 3 (17.6%): Lattice R0-R2 2 (11.8%), carve-out R3 1 (5.9%)
- islands R4: 14 (82.4%); needing the LLM: 0 (0.0%)
- by rung: R0 1, R1 0, R2 1, R3 1, R4 14
- original lines by outcome: {'lattice': 10, 'island': 151, 'carve_out': 11}; AST nodes: {'lattice': 83, 'island': 1018, 'carve_out': 71}
- tier mix: T0 3, T1 0, T2 0, T3 0 (islands 14); R3 carve-outs: 0 procedure, 0 native
- cost: $6.4940 (notes $0.0000), 17 LLM rounds, 398754 input / 614101 output tokens, 6528.537 s
- budget: exhausted -- the repo's cost cap ($6.00) is reached (spent $6.4940, 17 rounds)

## Routes

| route | request | status | rung | by | tier | carve | tally eq/rule/dev/mis | rounds | cost | reason |
|---|---|---|---|---|---|---|---|---|---|---|
| register | POST /auth/register | verified | R2 | llm | T0 |  | 9/3/0/0 | 5 | $0.0796 | no lifter matches the statements at line 17, 21 |
| login | POST /auth/login | island | R4 |  | island |  |  | 3 | $0.7575 | budget: the route's share of the repo budget ($0.25) is reached |
| place_order | POST /orders | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| list_own_orders | GET /orders/me | island | R4 |  | island |  |  | 1 | $1.3466 | cut off |
| get_order | GET /orders/{order_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| update_order_status | PATCH /orders/{order_id}/status | island | R4 |  | island |  |  | 1 | $1.3321 | cut off |
| flash_sale_purchase | POST /orders/flash-sale-purchase | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| create_product | POST /products | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| list_products | GET /products | island | R4 |  | island |  |  | 2 | $0.9605 | budget: the route's share of the repo budget ($0.31) is reached |
| get_product | GET /products/{product_id} | island | R4 |  | island |  |  | 3 | $0.5766 | budget: the route's share of the repo budget ($0.38) is reached |
| update_product | PATCH /products/{product_id} | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| read_own_profile | GET /users/me | verified | R0 | lifter | T0 |  | 9/0/0/0 | 0 | $0.0000 |  |
| update_own_profile | PATCH /users/me | island | R4 |  | island |  |  | 1 | $1.3213 | cut off |
| register_as_vendor | POST /vendors | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| read_own_vendor_profile | GET /vendors/me | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| update_own_vendor_profile | PATCH /vendors/me | island | R4 |  | island |  |  | 0 | $0.0000 | budget: the repo's cost cap ($6.00) is reached |
| verify_vendor | PATCH /vendors/{vendor_id}/verify | verified | R3 | llm | T0 |  | 11/2/0/0 | 1 | $0.1197 | no lifter matches the statements at line 59 |

## Islands

- `login` [budget]: budget: the route's share of the repo budget ($0.25) is reached
- `place_order` [budget]: budget: the repo's cost cap ($6.00) is reached
- `list_own_orders` [unsupported framework feature: dynamic dispatch]: cut off
- `get_order` [budget]: budget: the repo's cost cap ($6.00) is reached
- `update_order_status` [unsupported framework feature: dynamic dispatch]: cut off
- `flash_sale_purchase` [budget]: budget: the repo's cost cap ($6.00) is reached
- `create_product` [budget]: budget: the repo's cost cap ($6.00) is reached
- `list_products` [budget]: budget: the route's share of the repo budget ($0.31) is reached
- `get_product` [budget]: budget: the route's share of the repo budget ($0.38) is reached
- `update_product` [budget]: budget: the repo's cost cap ($6.00) is reached
- `update_own_profile` [unsupported framework feature: dynamic dispatch]: cut off
- `register_as_vendor` [budget]: budget: the repo's cost cap ($6.00) is reached
- `read_own_vendor_profile` [budget]: budget: the repo's cost cap ($6.00) is reached
- `update_own_vendor_profile` [budget]: budget: the repo's cost cap ($6.00) is reached

## Unclear profile decisions

- private OrderItem: no response model returns OrderItem; its private columns are not inferred
