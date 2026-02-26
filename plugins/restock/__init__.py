"""Kiroshop 补货插件"""

from plugins.restock.router import create_restock_router

PLUGIN_MANIFEST = {
    "id": "restock",
    "name": "自动补货",
    "description": "Kiroshop 商城补货代理，支持库存查询、订单管理、封禁检测",
    "version": "1.0.0",
    "icon": "ShoppingCart",
    "has_frontend": True,
    "api_prefix": "/plugins/restock",
}


def create_router():
    return create_restock_router()
