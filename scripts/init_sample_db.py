"""生成样例 SQLite 数据库 data/sample/demo.db"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "sample" / "demo.db"


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()

    with sqlite3.connect(DB) as conn:
        conn.executescript(
            """
            CREATE TABLE customers (
              id INTEGER PRIMARY KEY,
              name TEXT NOT NULL,
              region TEXT NOT NULL
            );
            CREATE TABLE orders (
              id INTEGER PRIMARY KEY,
              customer_id INTEGER NOT NULL,
              product TEXT NOT NULL,
              amount REAL NOT NULL,
              order_date TEXT NOT NULL,
              FOREIGN KEY(customer_id) REFERENCES customers(id)
            );

            INSERT INTO customers(id, name, region) VALUES
              (1, '星河科技', '华北'),
              (2, '云帆网络', '华东'),
              (3, '南岭数据', '华南'),
              (4, '西岭智造', '西南');

            INSERT INTO orders(id, customer_id, product, amount, order_date) VALUES
              (1, 1, '路由器', 12000, '2026-01-05'),
              (2, 2, '交换机', 18600, '2026-01-12'),
              (3, 3, '防火墙', 24000, '2026-01-20'),
              (4, 1, '路由器', 9800, '2026-02-03'),
              (5, 2, '光模块', 15200, '2026-02-11'),
              (6, 4, '交换机', 21000, '2026-02-18'),
              (7, 1, '防火墙', 17500, '2026-03-02'),
              (8, 3, '路由器', 13400, '2026-03-09'),
              (9, 2, '交换机', 19800, '2026-03-15'),
              (10, 1, '光模块', 8600, '2026-03-22');
            """
        )
    print(f"已生成: {DB}")


if __name__ == "__main__":
    main()
