from fastapi import APIRouter
import sqlalchemy
from src import database as db


router = APIRouter()


@router.get("/catalog/", tags=["catalog"])
def get_catalog():
  """
  Each unique item combination must have only a single price.
  """

  # Can return a max of 20 items.
  with db.engine.begin() as connection:
    potion_inventory = connection.execute(sqlalchemy.text("""
        SELECT *
        FROM potion_inventory
        ORDER BY num_potion DESC
        """)).fetchall()
    catalog = []
    for potion in potion_inventory:
      if len(catalog) < 6 and potion.num_potion != 0:
        catalog.append({
          "sku": potion.sku,
          "name": potion.sku,
          "quantity": potion.num_potion if potion.num_potion <= 10000 else 10000,
          "price": potion.price,
          "potion_type": potion.potion_type,
        })
  return catalog
