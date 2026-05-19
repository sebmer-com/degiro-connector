"""
Order models for DEGIRO Trading API
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum

class OrderAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LOSS = "STOP_LOSS" 
    STOP_LIMIT = "STOP_LIMIT"

class TimeType(str, Enum):
    DAY = "GOOD_TILL_DAY"
    GTC = "GOOD_TILL_CANCELED"

class OrderRequest(BaseModel):
    """Order request model with full DEGIRO functionality"""
    product_id: str = Field(..., description="Product ID to trade")
    action: OrderAction = Field(..., description="BUY or SELL")
    order_type: OrderType = Field(default=OrderType.LIMIT, description="Order type")
    quantity: float = Field(..., gt=0, description="Number of shares/units to trade")
    
    # Price parameters
    price: Optional[float] = Field(None, gt=0, description="Limit price (required for LIMIT and STOP_LIMIT orders)")
    stop_price: Optional[float] = Field(None, gt=0, description="Stop price (required for STOP_LOSS and STOP_LIMIT orders)")
    
    # Order settings
    time_type: TimeType = Field(default=TimeType.DAY, description="Order duration")
    
    # Optional metadata
    notes: Optional[str] = Field(None, max_length=500, description="Optional order notes")

class OrderResponse(BaseModel):
    """Order response after placement"""
    success: bool
    order_id: Optional[str] = None
    confirmation_id: Optional[str] = None
    message: str
    
    # Order details
    product_id: str
    action: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    stop_price: Optional[float] = None
    
    # Fees and costs
    estimated_fee: Optional[float] = None
    total_cost: Optional[float] = None
    
    # Timestamps
    created_at: str
    
class OrderCheckResponse(BaseModel):
    """Order validation response"""
    valid: bool
    confirmation_id: Optional[str] = None
    
    # Cost breakdown
    estimated_fee: Optional[float] = None
    total_cost: Optional[float] = None
    free_space_new: Optional[float] = None
    
    # Validation details
    message: str
    warnings: List[str] = []
    errors: List[str] = []

class OrderStatus(BaseModel):
    """Order status information"""
    order_id: str
    status: str  # PENDING, CONFIRMED, FILLED, CANCELLED, etc.
    product_id: str
    action: str
    order_type: str
    quantity: float
    price: Optional[float] = None
    filled_quantity: Optional[float] = None
    remaining_quantity: Optional[float] = None
    created_at: str
    updated_at: Optional[str] = None
