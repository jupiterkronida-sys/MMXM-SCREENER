"""
Confluence Screener Service
Orchestrates multi-asset scanning and returns signals for frontend plotting.
"""
import pandas as pd
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from confluence_engine import run_confluence_analysis, ConfluenceSignal

class ConfluenceScreenerService:
    def __init__(self, max_workers: int = 10):
        self.max_workers = max_workers
        
    def scan_universe(self, data_provider: callable, symbols: List[str]) -> List[Dict]:
        """
        Scan multiple assets and return all confluence signals.
        
        Args:
            data_provider: Function that takes symbol and returns DataFrame with OHLCV
            symbols: List of asset symbols to scan
            
        Returns:
            List of signal dictionaries ready for frontend consumption
        """
        all_signals = []
        
        def process_symbol(symbol):
            try:
                df = data_provider(symbol)
                if df is None or df.empty:
                    return []
                signals = run_confluence_analysis(df, symbol)
                return [s.to_dict() for s in signals]
            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                return []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_symbol = {executor.submit(process_symbol, sym): sym for sym in symbols}
            
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    results = future.result()
                    all_signals.extend(results)
                except Exception as e:
                    print(f"Failed to get result for {symbol}: {e}")
        
        # Sort by confluence score descending
        all_signals.sort(key=lambda x: x['confluence_score'], reverse=True)
        return all_signals
    
    def get_alert_summary(self, signals: List[Dict], min_score: float = 0.6) -> List[Dict]:
        """
        Filter signals for high-priority alerts only.
        Used for notifications/warnings.
        """
        return [s for s in signals if s['confluence_score'] >= min_score]

# Example usage for testing
if __name__ == "__main__":
    # Mock data provider (replace with real API call in production)
    def mock_data_provider(symbol):
        import numpy as np
        dates = pd.date_range(end=pd.Timestamp.now(), periods=600, freq='1h')
        base = 100 + np.random.randn(600).cumsum()
        return pd.DataFrame({
            'timestamp': dates,
            'open': base + np.random.randn(600) * 0.5,
            'high': base + np.abs(np.random.randn(600)) * 0.8,
            'low': base - np.abs(np.random.randn(600)) * 0.8,
            'close': base,
            'volume': np.random.randint(1000, 10000, 600)
        })
    
    screener = ConfluenceScreenerService()
    symbols = ['BTCUSD', 'ETHUSD', 'SOLUSD', 'BNBUSD']
    
    print("🔍 Running Confluence Scan...")
    signals = screener.scan_universe(mock_data_provider, symbols)
    
    if signals:
        print(f"\n✅ Found {len(signals)} confluence setups:")
        for sig in signals[:5]:  # Show top 5
            print(f"  • {sig['symbol']}: {sig['signal_type']} @ {sig['sr_zone_price']} "
                  f"(Score: {sig['confluence_score']}, Impact: {sig['estimated_bars_to_impact']} bars)")
    else:
        print("⚠️  No confluence signals found.")
