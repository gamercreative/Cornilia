"""
    The benchmark's fake tools, their in-memory data and every task.

    The tools only read the constant data below: no network, no files, no state that changes between runs.
    The "create" tools return a draft and store nothing, so every run sees the same data.
"""
import random
from dataclasses import dataclass, field
from typing import Annotated, Callable, Literal, TypedDict

from cornilia.tools import BuildToolRegistry, tool

from bench_scoring import (ABSENT, AllOf, Call, CodePasses, CountsAre, JsonMatches, Like, Mentions, NoCalls, NonEmpty,
                           NumberIs, OneOf)

# --- data ---

CUSTOMERS = {
    "C001": {"id": "C001", "name": "Rana Haddad", "city": "Beirut", "tier": "gold", "email": "rana.haddad@example.com", "lifetime_spend": 1240.50},
    "C002": {"id": "C002", "name": "Karim Saab", "city": "Tripoli", "tier": "silver", "email": "karim.saab@example.com", "lifetime_spend": 860.00},
    "C003": {"id": "C003", "name": "Lea Khoury", "city": "Byblos", "tier": "bronze", "email": "lea.khoury@example.com", "lifetime_spend": 1255.00},
    "C004": {"id": "C004", "name": "Omar Saleh", "city": "Beirut", "tier": "silver", "email": "omar.saleh@example.com", "lifetime_spend": 975.00},
    "C005": {"id": "C005", "name": "Maya Fares", "city": "Sidon", "tier": "gold", "email": "maya.fares@example.com", "lifetime_spend": 1249.75},
    "C006": {"id": "C006", "name": "Nour Hamdan", "city": "Zahle", "tier": "bronze", "email": "nour.hamdan@example.com", "lifetime_spend": 120.00},
}

PRODUCTS = {
    "SKU-101": {"sku": "SKU-101", "name": "USB-C cable", "price": 10.00},
    "SKU-204": {"sku": "SKU-204", "name": "Wireless mouse", "price": 25.00},
    "SKU-310": {"sku": "SKU-310", "name": "Keyboard", "price": 49.90},
    "SKU-415": {"sku": "SKU-415", "name": "Monitor stand", "price": 35.00},
    "SKU-520": {"sku": "SKU-520", "name": "Webcam", "price": 59.00},
    "SKU-610": {"sku": "SKU-610", "name": "Headset", "price": 34.50},
}

# (order id, customer, date, status, [(sku, quantity)]), the total is worked out from the product prices
_ORDERS = [
    ("O-1001", "C001", "2026-09-02", "paid", [("SKU-204", 1), ("SKU-101", 2)]),
    ("O-1002", "C001", "2026-09-15", "refunded", [("SKU-610", 1)]),
    ("O-1003", "C002", "2026-09-20", "paid", [("SKU-204", 4)]),
    ("O-1004", "C002", "2026-10-01", "paid", [("SKU-310", 2), ("SKU-101", 1)]),
    ("O-1005", "C002", "2026-10-28", "pending", [("SKU-520", 2), ("SKU-415", 1)]),
    ("O-1006", "C003", "2026-10-03", "paid", [("SKU-415", 1)]),
    ("O-1007", "C004", "2026-10-10", "paid", [("SKU-310", 1), ("SKU-520", 1), ("SKU-101", 1)]),
    ("O-1008", "C005", "2026-10-12", "paid", [("SKU-520", 1), ("SKU-310", 1)]),
    ("O-1009", "C001", "2026-11-01", "pending", [("SKU-310", 1), ("SKU-415", 1)]),
    ("O-1010", "C006", "2026-11-02", "paid", [("SKU-520", 1)]),
    ("O-1011", "C002", "2026-08-11", "paid", [("SKU-101", 3)]),
    ("O-1012", "C005", "2026-10-20", "paid", [("SKU-204", 2)]),
    ("O-1013", "C005", "2026-10-25", "refunded", [("SKU-610", 1)]),
]

ORDERS = {
    order_id: {"id": order_id, "customer_id": customer, "date": date, "status": status,
               "items": [{"sku": sku, "quantity": quantity} for sku, quantity in items],
               "total": round(sum(PRODUCTS[sku]["price"] * quantity for sku, quantity in items), 2)}
    for order_id, customer, date, status, items in _ORDERS
}

ISSUES = {
    issue_id: {"id": issue_id, "customer_id": customer, "status": status, "priority": priority, "title": title}
    for issue_id, customer, status, priority, title in [
        ("I-01", "C001", "open", "high", "Refund not received"),
        ("I-02", "C002", "closed", "low", "Change delivery address"),
        ("I-03", "C002", "open", "medium", "Mouse stopped working"),
        ("I-04", "C004", "pending", "medium", "Invoice missing VAT number"),
        ("I-05", "C004", "open", "low", "Question about warranty"),
        ("I-06", "C004", "closed", "high", "Wrong item delivered"),
        ("I-07", "C005", "open", "high", "Webcam driver crash"),
        ("I-08", "C006", "closed", "medium", "Late delivery"),
        ("I-09", "C003", "open", "low", "Keyboard key sticks"),
        ("I-10", "C006", "open", "high", "Package damaged"),
    ]
}

USD_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79}

def _Key(value) -> str:
    return str(value or "").strip().upper()

# --- tools ---

@tool
def get_customer(customer_id: Annotated[str, "Customer id, like C001"]) -> dict:
    """Gets one customer by id: name, city, tier, email and lifetime spend in USD."""
    return CUSTOMERS.get(_Key(customer_id)) or {"error": f"no customer {customer_id}"}

@tool
def find_customers(name: Annotated[str | None, "Part of the customer's name"] = None,
                   city: str | None = None, tier: Literal["gold", "silver", "bronze"] | None = None) -> list:
    """Finds customers by part of their name, by city or by tier. Leave a filter out to not filter on it."""
    return [customer for customer in CUSTOMERS.values()
            if (not name or name.casefold() in customer["name"].casefold())
            and (not city or city.casefold() == customer["city"].casefold())
            and (not tier or tier == customer["tier"])]

@tool
def get_product(sku: Annotated[str, "Product SKU, like SKU-101"]) -> dict:
    """Gets a product's name and price in USD."""
    return PRODUCTS.get(_Key(sku)) or {"error": f"no product {sku}"}

@tool
def get_products(skus: Annotated[list[str], "Product SKUs"]) -> list:
    """Gets several products at once: name and price in USD for each SKU."""
    return [PRODUCTS.get(_Key(sku)) or {"error": f"no product {sku}"} for sku in skus]

@tool
def get_order(order_id: Annotated[str, "Order id, like O-1001"]) -> dict:
    """Gets one order by id: customer id, date, status, items and total in USD."""
    return ORDERS.get(_Key(order_id)) or {"error": f"no order {order_id}"}

@tool
def get_orders(customer_id: str, status: Literal["paid", "pending", "refunded"] | None = None) -> list:
    """Lists a customer's orders, newest first. Give a status to see only orders with that status."""
    orders = [order for order in ORDERS.values()
              if order["customer_id"] == _Key(customer_id) and (not status or order["status"] == status)]
    return sorted(orders, key=lambda order: order["date"], reverse=True)

@tool
def list_issues(status: Literal["open", "closed", "pending"] | None = None, customer_id: str | None = None,
                priority: Literal["low", "medium", "high"] | None = None) -> list:
    """Lists support issues. Each filter is optional: status takes one value, leave it out to get every status."""
    return [issue for issue in ISSUES.values()
            if (not status or issue["status"] == status)
            and (not customer_id or issue["customer_id"] == _Key(customer_id))
            and (not priority or issue["priority"] == priority)]

@tool
def convert_currency(amount: float, from_currency: Literal["USD", "EUR", "GBP"],
                     to_currency: Literal["USD", "EUR", "GBP"]) -> dict:
    """Converts an amount between currencies at today's fixed rate."""
    if from_currency not in USD_RATES or to_currency not in USD_RATES:
        return {"error": f"supported currencies: {list(USD_RATES)}"}
    converted = round(amount / USD_RATES[from_currency] * USD_RATES[to_currency], 2)
    return {"amount": amount, "from": from_currency, "to": to_currency, "converted": converted}

class OrderLine(TypedDict):
    sku: Annotated[str, "Product SKU"]
    quantity: int

@tool
def create_order(customer_id: str, items: list[OrderLine]) -> dict:
    """Drafts an order for a customer and works out its total in USD. Nothing is saved."""
    if _Key(customer_id) not in CUSTOMERS:
        return {"error": f"no customer {customer_id}"}
    lines = []
    for item in items:
        product = PRODUCTS.get(_Key(item.get("sku")))
        if not product:
            return {"error": f"no product {item.get('sku')}"}
        quantity = int(item.get("quantity", 0))
        lines.append({"sku": product["sku"], "quantity": quantity, "line_total": round(product["price"] * quantity, 2)})
    return {"draft": True, "customer_id": _Key(customer_id), "lines": lines,
            "total": round(sum(line["line_total"] for line in lines), 2)}

class IssueDetails(TypedDict):
    priority: Literal["low", "medium", "high"]
    channel: Annotated[Literal["email", "phone", "chat"], "How the customer reported it"]

@tool
def create_issue(customer_id: str, title: str, details: IssueDetails, tags: list[str] | None = None) -> dict:
    """Drafts a support issue for a customer. Nothing is saved."""
    if _Key(customer_id) not in CUSTOMERS:
        return {"error": f"no customer {customer_id}"}
    return {"draft": True, "id": "I-11", "customer_id": _Key(customer_id), "title": title,
            "details": details, "tags": tags or [], "status": "open"}

class OrderFilter(TypedDict, total=False):
    """Every field is optional, a total range includes both ends."""
    customer_id: str
    status: Literal["paid", "pending", "refunded"]
    min_total: float
    max_total: float

@tool
def search_orders(filters: OrderFilter) -> list:
    """Searches all orders with a set of filters."""
    def keep(order):
        return ((not filters.get("customer_id") or order["customer_id"] == _Key(filters["customer_id"]))
                and (not filters.get("status") or order["status"] == filters["status"])
                and (filters.get("min_total") is None or order["total"] >= float(filters["min_total"]))
                and (filters.get("max_total") is None or order["total"] <= float(filters["max_total"])))
    return [order for order in ORDERS.values() if keep(order)]

ALL_TOOLS = [get_customer, find_customers, get_product, get_products, get_order, get_orders, list_issues, convert_currency,
             create_order, create_issue, search_orders]

def ToolRegistry() -> dict:
    return BuildToolRegistry(ALL_TOOLS)

SYSTEM_PROMPT = ("You are a support assistant for a small electronics shop. Use the tools to look up customers, "
                 "orders, products and issues; never guess an id or a number you can look up. Answer briefly. "
                 "When the user asks for an ANSWER: line, end your reply with it.")

# --- tasks ---

CATEGORIES = ["simple", "chain", "nested", "filters", "json", "coding", "reasoning", "long_context", "speed"]

# with --thinking compare these categories run a second time with thinking on
THINKING_CATEGORIES = ["coding", "reasoning"]

JSON_OBJECT = {"response_format": {"type": "json_object"}}

@dataclass
class Task:
    id: str
    category: str
    prompt: str | Callable[[], str] # long prompts are built when the task runs
    check: Callable[[str], tuple[bool, str]] # the final answer check
    calls: object = None # Call / OneOf / AllOf the run must contain, None when the task needs no tools
    tools: bool = True # whether the model gets the tools
    quick: bool = False # part of the quick suite
    response_configs: dict = field(default_factory=dict) # merged over the model's response configs
    expected_prompt_tokens: int | None = None # long-context tasks, to spot a server that cut the prompt
    repeat: bool = True # False runs the task once even with --repeats, for the slow long-context tasks

    def Prompt(self) -> str:
        return self.prompt() if callable(self.prompt) else self.prompt

ANSWER_LINE = "End your reply with a line 'ANSWER: {}'."

TASKS = [
    # simple: one call with the right arguments
    Task("simple/customer_city", "simple", "Which city does customer C003 live in?",
         Mentions("Byblos"), Call("get_customer", customer_id="C003"), quick=True),
    Task("simple/product_price", "simple", "How much does product SKU-204 cost?",
         NumberIs(25), Call("get_product", sku="SKU-204")),
    Task("simple/order_status", "simple", "What is the status of order O-1005?",
         Mentions("pending"), Call("get_order", order_id="O-1005")),
    Task("simple/convert", "simple", "Convert 250 USD to EUR.",
         NumberIs(230), Call("convert_currency", amount=250, from_currency="USD", to_currency="EUR"), quick=True),
    Task("simple/city_filter", "simple", "Which customers live in Beirut?",
         Mentions("Rana Haddad", "Omar Saleh"), Call("find_customers", city="Beirut")),
    Task("simple/list_argument", "simple", "Look up SKU-101, SKU-415 and SKU-520 in one call and give me their names.",
         Mentions("USB-C cable", "Monitor stand", "Webcam"), Call("get_products", skus=["SKU-101", "SKU-415", "SKU-520"])),
    Task("simple/enum", "simple", "List the ids of all high-priority issues.",
         Mentions("I-01", "I-06", "I-07", "I-10", forbidden=("I-03", "I-05")), Call("list_issues", priority="high")),
    Task("simple/two_calls", "simple", "In which cities do customers C001 and C002 live?",
         Mentions("Beirut", "Tripoli"), AllOf(Call("get_customer", customer_id="C001"), Call("get_customer", customer_id="C002"))),
    Task("simple/no_tool_needed", "simple", "What is 12 times 12? No need to look anything up.",
         NumberIs(144), NoCalls()),

    # chain: the result of one call is the input of the next
    Task("chain/paid_total", "chain", "How much has Maya Fares spent on paid orders in total, in USD?",
         NumberIs(158.90), AllOf(Call("find_customers", name=Like("maya")), Call("get_orders", customer_id="C005"))),
    Task("chain/order_email", "chain", "What is the email address of the customer who placed order O-1007?",
         Mentions("omar.saleh@example.com"), AllOf(Call("get_order", order_id="O-1007"), Call("get_customer", customer_id="C004")),
         quick=True),
    Task("chain/recent_order_eur", "chain", "What is the total of customer C002's most recent order, in EUR?",
         NumberIs(140.76), AllOf(Call("get_orders", customer_id="C002"),
                                 Call("convert_currency", amount=153, from_currency="USD", to_currency="EUR"))),
    Task("chain/misspelled_name", "chain",
         "How many orders has Maya Faris placed? If a search finds nobody, try a shorter part of the name.",
         NumberIs(3, tolerance=0), Call("get_orders", customer_id="C005")),
    Task("chain/zahle_issue", "chain",
         "Our customer in Zahle has an open high-priority issue. What is its title?",
         Mentions("Package damaged"), AllOf(Call("find_customers", city="Zahle"),
                                            OneOf(Call("list_issues", customer_id="C006"), Call("list_issues", status="open"))),
         quick=True),

    # nested: a TypedDict or a list of objects as an argument
    Task("nested/order_lines", "nested", "Draft an order for customer C003: 2 x SKU-101 and 1 x SKU-204. What is the total?",
         NumberIs(45), Call("create_order", customer_id="C003",
                            items=[{"sku": "SKU-101", "quantity": 2}, {"sku": "SKU-204", "quantity": 1}]), quick=True),
    Task("nested/issue_details", "nested",
         "Open a support issue for customer C005 titled 'Webcam flickers'. It is high priority, the customer called by "
         "phone, and tag it hardware and video.",
         Mentions("I-11"), Call("create_issue", customer_id="C005", title=Like("webcam flickers"),
                                details={"priority": "high", "channel": "phone"}, tags=["hardware", "video"]), quick=True),
    Task("nested/lookup_then_order", "nested",
         "Draft an order for Lea Khoury with three keyboards (SKU-310) and one monitor stand (SKU-415), and tell me the total.",
         NumberIs(184.70), Call("create_order", customer_id="C003",
                                items=[{"sku": "SKU-310", "quantity": 3}, {"sku": "SKU-415", "quantity": 1}])),
    Task("nested/search_filters", "nested", "Find every paid order with a total between 50 and 120 USD. List their ids.",
         Mentions("O-1003", "O-1004", "O-1007", "O-1008", "O-1010", "O-1012", forbidden=("O-1001", "O-1006", "O-1011")),
         Call("search_orders", filters={"status": "paid", "min_total": 50, "max_total": 120})),

    # filters and comparisons, the weak spots in the reference run
    Task("filters/open_and_closed", "filters", "How many support issues are open, and how many are closed?",
         CountsAre(open=6, closed=3),
         OneOf(AllOf(Call("list_issues", status="open"), Call("list_issues", status="closed")),
               Call("list_issues", status=ABSENT, customer_id=ABSENT, priority=ABSENT)), quick=True),
    Task("filters/open_or_pending", "filters", "List the ids of every issue of customer C004 that is open or pending.",
         Mentions("I-04", "I-05", forbidden=("I-06",)),
         OneOf(AllOf(Call("list_issues", status="open"), Call("list_issues", status="pending")),
               Call("list_issues", customer_id="C004", status=ABSENT)), quick=True),
    Task("filters/highest_spend", "filters",
         "Of customers C001, C003 and C005, which one has the highest lifetime spend? " + ANSWER_LINE.format("<customer id>"),
         Mentions("C003", forbidden=("C001", "C005"), final_only=True),
         AllOf(Call("get_customer", customer_id="C001"), Call("get_customer", customer_id="C003"),
               Call("get_customer", customer_id="C005")), quick=True),
    Task("filters/cheapest_of_three", "filters",
         "Which is cheapest: SKU-415, SKU-610 or SKU-310? " + ANSWER_LINE.format("<sku>"),
         Mentions("SKU-610", forbidden=("SKU-415", "SKU-310"), final_only=True),
         AllOf(Call("get_product", sku="SKU-415"), Call("get_product", sku="SKU-610"), Call("get_product", sku="SKU-310")),
         quick=True),
    Task("filters/over_threshold", "filters",
         "How many of customer C002's orders have a total over 100 USD? " + ANSWER_LINE.format("<number>"),
         NumberIs(2, tolerance=0), OneOf(Call("get_orders", customer_id="C002"), Call("search_orders", filters={"customer_id": "C002"}))),
    Task("filters/not_paid", "filters",
         "Which of customer C001's orders are not paid? " + ANSWER_LINE.format("<order ids>"),
         Mentions("O-1002", "O-1009", forbidden=("O-1001",), final_only=True), Call("get_orders", customer_id="C001")),

    # json output, with response_format and with the prompt only
    Task("json/format_flat", "json",
         "A customer wrote: 'Hi, I'm Nadia Karam from Jounieh, a gold member since 2021.' Return a JSON object with the "
         "keys name (string), city (string), tier (lowercase string) and since (integer).",
         JsonMatches({"name": "Nadia Karam", "city": "Jounieh", "tier": "gold", "since": 2021}, strict=True),
         tools=False, quick=True, response_configs=JSON_OBJECT),
    Task("json/format_nested", "json",
         "Products: USB-C cable is SKU-101, webcam is SKU-520, wireless mouse is SKU-204. A customer wants two USB-C "
         "cables, one webcam and three wireless mice. Return a JSON object {\"items\": [{\"sku\": ..., \"quantity\": ...}]}.",
         JsonMatches({"items": [{"sku": "SKU-101", "quantity": 2}, {"sku": "SKU-520", "quantity": 1},
                                {"sku": "SKU-204", "quantity": 3}]}, strict=True),
         tools=False, response_configs=JSON_OBJECT),
    Task("json/prompt_flat", "json",
         "A customer wrote: 'Good morning, this is Fadi Mansour in Tyre, silver tier, customer since 2019.' Reply with "
         "only a JSON object, no other text, with the keys name (string), city (string), tier (lowercase string) and "
         "since (integer).",
         JsonMatches({"name": "Fadi Mansour", "city": "Tyre", "tier": "silver", "since": 2019}), tools=False, quick=True),
    Task("json/prompt_nested", "json",
         "Products: keyboard is SKU-310, headset is SKU-610, monitor stand is SKU-415. A customer wants one keyboard, "
         "four headsets and two monitor stands. Reply with only a JSON object {\"items\": [{\"sku\": ..., \"quantity\": ...}]}, "
         "no other text.",
         JsonMatches({"items": [{"sku": "SKU-310", "quantity": 1}, {"sku": "SKU-610", "quantity": 4},
                                {"sku": "SKU-415", "quantity": 2}]}), tools=False),

    # coding: the reply's function is checked against asserts it never sees
    Task("coding/palindrome", "coding",
         "Write a Python function is_palindrome(text: str) -> bool that ignores case, spaces and punctuation. "
         + "Reply with only the code in one ```python block.",
         CodePasses("is_palindrome", ["FUNCTION('A man, a plan, a canal: Panama') is True", "FUNCTION('race a car') is False",
                                      "FUNCTION('') is True", "FUNCTION('No lemon, no melon') is True"]), tools=False, quick=True),
    Task("coding/word_counts", "coding",
         "Write a Python function word_counts(text: str) -> dict that counts each word, case-insensitive, ignoring "
         "punctuation, and returns {word: count}. Reply with only the code in one ```python block.",
         CodePasses("word_counts", ["FUNCTION('The cat and the hat.') == {'the': 2, 'cat': 1, 'and': 1, 'hat': 1}",
                                    "FUNCTION('') == {}", "FUNCTION('Go, go, GO!') == {'go': 3}"]), tools=False),
    Task("coding/merge_intervals", "coding",
         "Write a Python function merge_intervals(intervals: list[list[int]]) -> list[list[int]] that merges overlapping "
         "or touching intervals and returns them sorted. Reply with only the code in one ```python block.",
         CodePasses("merge_intervals", ["FUNCTION([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]",
                                        "FUNCTION([[1, 4], [4, 5]]) == [[1, 5]]", "FUNCTION([]) == []",
                                        "FUNCTION([[5, 7], [1, 2]]) == [[1, 2], [5, 7]]"]), tools=False),
    Task("coding/roman", "coding",
         "Write a Python function roman_to_int(s: str) -> int that converts a Roman numeral to an integer. "
         "Reply with only the code in one ```python block.",
         CodePasses("roman_to_int", ["FUNCTION('III') == 3", "FUNCTION('LVIII') == 58", "FUNCTION('MCMXCIV') == 1994",
                                     "FUNCTION('XLIX') == 49"]), tools=False),
    Task("coding/flatten", "coding",
         "Write a Python function flatten(nested: list) -> list that flattens lists nested to any depth, keeping the "
         "order. Reply with only the code in one ```python block.",
         CodePasses("flatten", ["FUNCTION([1, [2, [3, [4]], 5]]) == [1, 2, 3, 4, 5]", "FUNCTION([]) == []",
                                "FUNCTION([[], [[]], 6]) == [6]", "FUNCTION(['ab', ['c']]) == ['ab', 'c']"]), tools=False),
    Task("coding/second_largest", "coding",
         "Write a Python function second_largest(numbers: list[int]) -> int | None that returns the second largest "
         "distinct value, or None when there is none. Reply with only the code in one ```python block.",
         CodePasses("second_largest", ["FUNCTION([3, 1, 4, 4, 2]) == 3", "FUNCTION([5, 5]) is None", "FUNCTION([]) is None",
                                       "FUNCTION([-1, -7, -3]) == -3"]), tools=False),

    # reasoning without tools
    Task("reasoning/arrival_time", "reasoning",
         "A train leaves at 14:35 and the trip takes 2 hours 50 minutes. At what time does it arrive? "
         + ANSWER_LINE.format("<HH:MM in 24-hour time>"), Mentions("17:25", final_only=True), tools=False),
    Task("reasoning/unit_price", "reasoning",
         "Pens are sold at 3 for 4 USD. How much do 15 pens cost, in USD? " + ANSWER_LINE.format("<number>"),
         NumberIs(20), tools=False),
    Task("reasoning/ordering", "reasoning",
         "Rana is older than Karim. Lea is younger than Karim. Omar is older than Rana. Who is the second oldest? "
         + ANSWER_LINE.format("<name>"), Mentions("Rana", forbidden=("Omar", "Karim", "Lea"), final_only=True),
         tools=False, quick=True),
    Task("reasoning/discount_tax", "reasoning",
         "A shirt costs 40 USD. It is discounted by 25%, then 10% tax is added to the discounted price. What is the "
         "final price in USD? " + ANSWER_LINE.format("<number>"), NumberIs(33), tools=False, quick=True),
    Task("reasoning/weekday", "reasoning",
         "If today is Wednesday, what day of the week is it 10 days from now? " + ANSWER_LINE.format("<day>"),
         Mentions("Saturday", final_only=True), tools=False),
    Task("reasoning/bat_and_ball", "reasoning",
         "A bat and a ball cost 1.10 USD together. The bat costs 1.00 USD more than the ball. How many cents does the "
         "ball cost? " + ANSWER_LINE.format("<number of cents>"), NumberIs(5, tolerance=0), tools=False),
]

# --- long context: a fact planted in deterministic filler text ---

_SUBJECTS = ["The north team", "The night shift", "Our driver", "The south team", "The packing crew", "The new intern",
             "The warehouse manager", "The east team", "Support", "The courier"]
_VERBS = ["moved", "counted", "labelled", "checked", "repacked", "loaded", "stacked", "inspected", "sorted", "scanned"]
_OBJECTS = ["the blue crates", "the spare cables", "the returned mice", "the damaged boxes", "the keyboard pallets",
            "the webcam stock", "the monitor stands", "the headset cartons", "the empty bins", "the shelf labels"]
_PLACES = ["near the loading dock", "in aisle seven", "before the morning delivery", "after lunch", "by the back door",
           "on the top shelf", "next to the office", "at the end of the day", "in the cold room", "behind the counter"]

WORDS_PER_TOKEN = 0.75 # plain English words, roughly. the real count comes from the provider's usage report

def Filler(rng: random.Random, words: int) -> list[str]:
    sentences, count = [], 0
    while count < words:
        sentence = f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} {rng.choice(_OBJECTS)} {rng.choice(_PLACES)}."
        sentences.append(sentence)
        count += len(sentence.split())
    return sentences

# a log of about `tokens` tokens with the code for the north warehouse at `depth` (0 = start, 1 = end),
# and the codes of two other warehouses elsewhere as distractors
def NeedlePrompt(tokens: int, depth: float, code: str, distractors: tuple[str, str], seed: int) -> str:
    rng = random.Random(seed)
    sentences = Filler(rng, int(tokens * WORDS_PER_TOKEN))
    planted = [
        (depth, f"Note from the manager: the backup door code for the north warehouse is {code}."),
        ((depth + 0.4) % 1, f"Note from the manager: the backup door code for the south warehouse is {distractors[0]}."),
        ((depth + 0.6) % 1, f"Note from the manager: the backup door code for the east warehouse is {distractors[1]}."),
    ]
    for position, sentence in sorted(planted, reverse=True):
        sentences.insert(int(len(sentences) * position), sentence)
    log = "\n".join(" ".join(sentences[index:index + 6]) for index in range(0, len(sentences), 6))
    return ("Below is the shop's daily work log.\n\n<log>\n" + log + "\n</log>\n\n"
            "What is the backup door code for the north warehouse? " + ANSWER_LINE.format("<code>"))

def _LongTask(tokens: int, depth: float, code: str, distractors: tuple[str, str], seed: int) -> Task:
    return Task(f"long_context/{tokens // 1000}k_at_{round(depth * 100)}pct", "long_context",
                lambda: NeedlePrompt(tokens, depth, code, distractors, seed),
                Mentions(code, forbidden=distractors, final_only=True), tools=False, expected_prompt_tokens=tokens,
                repeat=False)

# about 2k, 7k, 14k and 24k tokens, with the fact at 10%, 50% and 90% of the log. each task has its own codes
LONG_CONTEXT_SIZES = [2000, 7000, 14000, 24000]
LONG_CONTEXT_DEPTHS = [0.1, 0.5, 0.9]

def _LongTasks() -> list[Task]:
    tasks = []
    for size in LONG_CONTEXT_SIZES:
        for depth in LONG_CONTEXT_DEPTHS:
            seed = size + int(depth * 100)
            codes = random.Random(seed).sample(range(1000, 10000), 3)
            code, *distractors = (str(number) for number in codes)
            tasks.append(_LongTask(size, depth, code, tuple(distractors), seed))
    return tasks

TASKS += _LongTasks()

# speed: replies of about 8, 380 and 1200 tokens, only the timing matters
TASKS += [
    Task("speed/short_reply", "speed", "Reply with exactly the words: the benchmark is running", NonEmpty(3), tools=False),
    Task("speed/medium_reply", "speed", "Write about 280 words on how a small shop should handle returned items.",
         NonEmpty(150), tools=False),
    Task("speed/long_reply", "speed",
         "Write a detailed guide of about 900 words on organising a small electronics warehouse: layout, labelling, "
         "stock counts, returns and safety.", NonEmpty(500), tools=False),
]

# the tasks a suite runs, optionally only some categories
def SelectTasks(suite: str, only: list[str] | None = None) -> list[Task]:
    if suite not in ("quick", "full"):
        raise ValueError(f"unknown suite {suite!r}, use quick or full")
    unknown = [name for name in only or [] if name not in CATEGORIES]
    if unknown:
        raise ValueError(f"unknown categories {unknown}, choose from {CATEGORIES}")
    tasks = [task for task in TASKS if suite == "full" or task.quick]
    if only:
        tasks = [task for task in tasks if task.category in only]
        if not tasks:
            raise ValueError(f"the {suite} suite has no tasks in {only}, try --suite full")
    return tasks
