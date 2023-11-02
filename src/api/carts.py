from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from src.api import auth
import sqlalchemy
from src import database as db
from datetime import datetime
import pytz
from enum import Enum

router = APIRouter(
  prefix="/carts",
  tags=["cart"],
  dependencies=[Depends(auth.get_api_key)],
)


convert_days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class search_sort_options(str, Enum):
    customer_name = "customer_name"
    item_sku = "item_sku"
    line_item_total = "line_item_total"
    timestamp = "timestamp"


class search_sort_order(str, Enum):
    asc = "asc"
    desc = "desc"   


@router.get("/search/", tags=["search"])
def search_orders(
    customer_name: str = "",
    potion_sku: str = "",
    search_page: str = "",
    sort_col: search_sort_options = search_sort_options.timestamp,
    sort_order: search_sort_order = search_sort_order.desc,
):
    """
    Search for cart line items by customer name and/or potion sku.

    Customer name and potion sku filter to orders that contain the 
    string (case insensitive). If the filters aren't provided, no
    filtering occurs on the respective search term.

    Search page is a cursor for pagination. The response to this
    search endpoint will return previous or next if there is a
    previous or next page of results available. The token passed
    in that search response can be passed in the next search request
    as search page to get that page of results.

    Sort col is which column to sort by and sort order is the direction
    of the search. They default to searching by timestamp of the order
    in descending order.

    The response itself contains a previous and next page token (if
    such pages exist) and the results as an array of line items. Each
    line item contains the line item id (must be unique), item sku, 
    customer name, line item total (in gold), and timestamp of the order.
    Your results must be paginated, the max results you can return at any
    time is 5 total line items.
    """
    with db.engine.begin() as connection:
      where_message = ""
      if customer_name != "" and potion_sku != "":
        where_message = f"WHERE carts.customer ILIKE '%{customer_name}%' AND cart_items.sku ILIKE '%{potion_sku}%'"
      elif customer_name != "":
        where_message = f"WHERE carts.customer ILIKE '%{customer_name}%'"
      elif potion_sku != "":
        where_message = f"WHERE cart_items.sku ILIKE '%{potion_sku}%'"
      current = 0 if (search_page == "") else int(search_page)
      cart_items = connection.execute(sqlalchemy.text(f"""
          SELECT
            cart_items.items_id as line_item_id,
            cart_items.sku as item_sku,
            carts.customer as customer_name,
            global_inventory_entries.change_gold as line_item_total,
            global_inventory_transactions.created_at as timestamp
          FROM cart_items
          JOIN carts on cart_items.cart_id = carts.cart_id
          JOIN global_inventory_transactions on carts.global_inventory_transaction_id = global_inventory_transactions.id
          JOIN global_inventory_entries on global_inventory_entries.global_inventory_transaction_id = global_inventory_transactions.id
          {where_message}
          ORDER BY {sort_col.value} {sort_order.value}
          LIMIT 6
          OFFSET {str(current)}
          """)).fetchall()
    results = []
    for item in cart_items:
      if len(results) < 5:
        results.append({
            "line_item_id": item.line_item_id,
            "item_sku": item.item_sku,
            "customer_name": item.customer_name,
            "line_item_total": item.line_item_total,
            "timestamp": item.timestamp,
        })
    previous = current - 5
    previous = "" if previous < 0 else str(previous)
    next = current + 5
    next = "" if len(cart_items) < 6 else str(next) 
    return {
        "previous": previous,
        "next": next,
        "results": results
    }


class NewCart(BaseModel):
  customer: str


@router.post("/")
def create_cart(new_cart: NewCart):
  """ """
  with db.engine.begin() as connection:
    cart_id = connection.execute(sqlalchemy.text("""
        INSERT INTO carts (customer)
        VALUES (:customer)
        RETURNING cart_id
        """), {"customer": new_cart.customer}).first().cart_id
  return {"cart_id": cart_id}


@router.get("/{cart_id}")
def get_cart(cart_id: int):
  """ """
  with db.engine.begin() as connection:
    day = convert_days[datetime.utcnow().weekday()]
    message = ""
    cart = connection.execute(sqlalchemy.text("""
        SELECT *
        FROM carts
        WHERE cart_id = :cart_id
        """), {"cart_id": cart_id}).first()
    # cart has been created
    if cart:
      cart_items = connection.execute(sqlalchemy.text("""
          SELECT *
          FROM cart_items
          WHERE cart_id = :cart_id
          """), {"cart_id": cart_id}).fetchall()
      # cart has been set
      if cart_items:
        # cart has been checked out
        if cart.payment:
          message = f"Cart #{cart.cart_id}: {cart.customer} used {cart.payment} to buy:"
        # cart has not been checked out
        else:
          message = f"Cart #{cart.cart_id}: {cart.customer} is seeking to buy:"
        for cart_item in cart_items:
          potion = connection.execute(sqlalchemy.text(f"""
              SELECT potions.{day}_price as price, potions.potion_type, COALESCE(SUM(change), 0) as num_potion
              FROM potions
              LEFT JOIN potion_entries ON potion_entries.potion_sku = potions.sku
              WHERE potions.sku = :sku
              GROUP BY potions.{day}_price, potions.potion_type
              """), {"sku": cart_item.sku}).first()
          message += f" {cart_item.quantity} {cart_item.sku} ({potion.potion_type}) "\
                     f"for {cart_item.quantity * potion.price} gold "\
                     f"({potion.num_potion} remaining),"
        return message[:-1] + "."
    # cart has not been created
    else:
      return f"Cart #{cart_id} has not yet been created."


class CartItem(BaseModel):
  quantity: int


# don't know if cart_id and item_id are supposed to be diff. multiple items in 1 cart?
@router.post("/{cart_id}/items/{item_sku}")
def set_item_quantity(cart_id: int, item_sku: str, cart_item: CartItem):
  """ """
  with db.engine.begin() as connection:
    connection.execute(sqlalchemy.text("""
        INSERT INTO cart_items (cart_id, sku, quantity)
        VALUES (:cart_id, :sku, :quantity)
        """), {"cart_id": cart_id, "sku": item_sku, "quantity": cart_item.quantity})
  return "OK"


class CartCheckout(BaseModel):
  payment: str


@router.post("/{cart_id}/checkout")
def checkout(cart_id: int, cart_checkout: CartCheckout):
  """ """
  with db.engine.begin() as connection:
    day = convert_days[datetime.utcnow().weekday()]
    total_potions_bought = 0
    total_gold_paid = 0
    global_transaction_id = connection.execute(sqlalchemy.text("""
        WITH transaction AS (
            INSERT INTO global_inventory_transactions DEFAULT VALUES
            RETURNING id
        )
        UPDATE carts c
        SET payment = :payment, global_inventory_transaction_id = t.id
        FROM transaction t
        WHERE c.cart_id = :cart_id
        RETURNING t.id
    """), {"payment": cart_checkout.payment, "cart_id": cart_id}).first().id
    # get cart
    cart = connection.execute(sqlalchemy.text("""
        SELECT sku, quantity
        FROM cart_items
        WHERE cart_id = :cart_id
        """), {"cart_id": cart_id}).fetchall()
    for cart_items in cart:
      potion = connection.execute(sqlalchemy.text("""
          SELECT *
          FROM potions
          WHERE sku = :sku
          """), {"sku": cart_items.sku}).first()
      total_potions_bought += cart_items.quantity
      total_gold_paid += cart_items.quantity * getattr(potion, day + "_price")
      # update potion_inventory
      connection.execute(sqlalchemy.text("""
        WITH transaction AS (
            INSERT INTO potion_transactions (description)
            VALUES (:description)
            RETURNING id
        )
        INSERT INTO potion_entries (potion_sku, change, potion_transaction_id)
        SELECT :potion_sku, :change, t.id
        FROM transaction t
          """), {"description": get_cart(cart_id), "potion_sku": cart_items.sku, "change": -cart_items.quantity})
      # update potions num_sold for day
      #TODO maybe make this have ledger? not necessary since already logged
      connection.execute(sqlalchemy.text(f"""
          UPDATE potions
          SET {day}_sold = {day}_sold + :num_sold
          WHERE sku = :sku
          """), {"num_sold": cart_items.quantity, "sku": cart_items.sku})
    # update global_inventory
    connection.execute(sqlalchemy.text("""
        UPDATE global_inventory_transactions
        SET description = :description
        WHERE id = :transaction_id
        """), {"description": get_cart(cart_id), "transaction_id": global_transaction_id})
    connection.execute(sqlalchemy.text("""
        INSERT INTO global_inventory_entries (change_gold, global_inventory_transaction_id)
        VALUES (:total_gold_paid, :transaction_id)
        """), {"total_gold_paid": total_gold_paid, "transaction_id": global_transaction_id})
    connection.commit()
    return {"total_potions_bought": total_potions_bought, "total_gold_paid": total_gold_paid}
