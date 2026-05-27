"""
FastAPI Backend for Confluence Screener
Provides REST API endpoints for frontend to fetch signals and plot them.
BACKEND ONLY: No plotting here, just data delivery.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict
import pandas as pd
from confluence_engine import run_confluence_analysis
from screener_service import ConfluenceScreenerService

app = FastAPI(title="MMXM Confluence Screener API")

# Enable CORS for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize service
screener = ConfluenceScreenerService()

# Mock data provider (Replace with your actual MMXM data source)
def get_market_data(symbol: str) -> pd.DataFrame:
    """
    TODO: Replace this with actual call to MMXM database or exchange API.
    Currently returns mock data for demonstration.
    """
    import numpy as np
    # Generate realistic-looking OHLCV data
    periods = 600
    dates = pd.date_range(end=pd.Timestamp.now(), periods=periods, freq='1h')
    
    # Create trending price with noise
    base_price = 50000 if 'BTC' in symbol else 3000 if 'ETH' in symbol else 100
    trend = np.linspace(0, base_price * 0.1, periods)
    noise = np.random.randn(periods).cumsum() * (base_price * 0.001)
    close = base_price + trend + noise
    
    return pd.DataFrame({
        'timestamp': dates,
        'open': close + np.random.randn(periods) * (base_price * 0.0005),
        'high': close + np.abs(np.random.randn(periods)) * (base_price * 0.002),
        'low': close - np.abs(np.random.randn(periods)) * (base_price * 0.002),
        'close': close,
        'volume': np.random.randint(100, 10000, periods)
    })

@app.get("/api/confluence/signals", response_model=List[Dict])
async def get_all_signals(symbols: str = "BTCUSD,ETHUSD,SOLUSD"):
    """
    Get all confluence signals for specified symbols.
    Returns data for frontend to plot signals.
    
    Query params:
        symbols: Comma-separated list of symbols (e.g., "BTCUSD,ETHUSD")
    """
    try:
        symbol_list = [s.strip() for s in symbols.split(",")]
        signals = screener.scan_universe(get_market_data, symbol_list)
        return signals
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/confluence/alerts", response_model=List[Dict])
async def get_high_priority_alerts(symbols: str = "BTCUSD,ETHUSD,SOLUSD", min_score: float = 0.6):
    """
    Get only high-priority confluence alerts (warnings).
    Use this endpoint for notifications/popups.
    
    Query params:
        symbols: Comma-separated list of symbols
        min_score: Minimum confluence score threshold (0.0-1.0)
    """
    try:
        symbol_list = [s.strip() for s in symbols.split(",")]
        all_signals = screener.scan_universe(get_market_data, symbol_list)
        alerts = screener.get_alert_summary(all_signals, min_score=min_score)
        return alerts
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/confluence/symbol/{symbol}", response_model=List[Dict])
async def get_signals_for_symbol(symbol: str):
    """
    Get confluence signals for a single specific symbol.
    Useful for individual chart pages.
    """
    try:
        df = get_market_data(symbol)
        signals = run_confluence_analysis(df, symbol)
        return [s.to_dict() for s in signals]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "mmxm-confluence-screener"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
