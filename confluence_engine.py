"""
Temporal Liquidity Confluence Engine
Combines Analogue Matcher (Future Shadow) with Horizontal S/R Zones.
BACKEND ONLY: Returns data, does not plot.
"""
import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

@dataclass
class SRZone:
    price: float
    strength: float
    type: str  # 'support' or 'resistance'
    touches: int
    upper_bound: float
    lower_bound: float

@dataclass
class FutureShadow:
    bars_ahead: List[int]
    projected_prices: List[float]
    confidence: float
    direction: str  # 'bull' or 'bear'

@dataclass
class ConfluenceSignal:
    timestamp: datetime
    symbol: str
    current_price: float
    signal_type: str  # 'bounce_long', 'rejection_short', 'breakout_up', 'breakout_down'
    confluence_score: float  # 0.0 to 1.0
    sr_zone_price: float
    sr_zone_type: str
    shadow_direction: str
    estimated_bars_to_impact: int
    projection_path: List[Tuple[int, float]]  # For frontend plotting
    
    def to_dict(self):
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "current_price": self.current_price,
            "signal_type": self.signal_type,
            "confluence_score": round(self.confluence_score, 2),
            "sr_zone_price": round(self.sr_zone_price, 4),
            "sr_zone_type": self.sr_zone_type,
            "shadow_direction": self.shadow_direction,
            "estimated_bars_to_impact": self.estimated_bars_to_impact,
            "projection_path": [{"bar": b, "price": p} for b, p in self.projection_path]
        }

class PatternEngine:
    """Ported logic from GainzAlgo Analogue Matcher"""
    
    @staticmethod
    def linear_regression_slope(y: np.ndarray, x: np.ndarray) -> float:
        if len(y) < 2: return 0.0
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        num = np.sum((x - x_mean) * (y - y_mean))
        den = np.sum((x - x_mean) ** 2)
        return num / den if den != 0 else 0.0

    @staticmethod
    def find_best_analogue(close: np.ndarray, time_idx: np.ndarray, window: int, lookback: int, tol: float) -> Optional[Dict]:
        if len(close) < lookback + window:
            return None
            
        current_slope = PatternEngine.linear_regression_slope(
            close[-window:], 
            np.arange(window)
        )
        
        best_diff = float('inf')
        best_idx = -1
        best_r2 = 0.0
        best_direction = ""
        
        # Simplified R² calculation for performance
        for i in range(lookback):
            start = len(close) - lookback - window + i
            end = start + window
            if end > len(close) - window: break
            
            segment = close[start:end]
            hist_slope = PatternEngine.linear_regression_slope(segment, np.arange(window))
            
            diff = abs(current_slope - hist_slope)
            
            if diff <= tol and diff < best_diff:
                # Determine direction based on what happened AFTER the historical match
                future_perf = close[end] - close[start]
                best_diff = diff
                best_idx = start
                best_direction = "bull" if future_perf > 0 else "bear"
                
                # Simple R2 approx
                corr = np.corrcoef(segment, np.arange(window))[0, 1]
                best_r2 = corr**2 if not np.isnan(corr) else 0
                
        if best_idx == -1:
            return None
            
        return {
            "idx": best_idx,
            "direction": best_direction,
            "r2": best_r2,
            "diff": best_diff
        }

    @staticmethod
    def generate_future_shadow(close: np.ndarray, match_data: Dict, window: int) -> FutureShadow:
        if not match_data:
            return None
            
        hist_start = match_data["idx"]
        hist_end = hist_start + window
        
        current_price = close[-1]
        hist_anchor = close[hist_start]
        
        # Calculate relative moves from the historical match point forward
        slope = PatternEngine.linear_regression_slope(close[-window:], np.arange(window))
        volatility = np.std(close[-window:])
        
        projected_prices = []
        bars_ahead = list(range(1, window + 1))
        
        last_price = current_price
        for i in range(window):
            # Deterministic trend + random walk component scaled by hist volatility
            move = slope + (volatility * 0.5 * np.sin(i/5)) # Simplified curvature
            next_price = last_price + move
            projected_prices.append(next_price)
            last_price = next_price
            
        return FutureShadow(
            bars_ahead=bars_ahead,
            projected_prices=projected_prices,
            confidence=match_data["r2"],
            direction=match_data["direction"]
        )

class SRDetector:
    """Memory Efficient Horizontal S/R Detection using Rolling Windows"""
    
    @staticmethod
    def identify_zones(close: np.ndarray, high: np.ndarray, low: np.ndarray, window: int = 200) -> List[SRZone]:
        if len(close) < window:
            return []
            
        # Use only the recent window to save memory
        recent_close = close[-window:]
        recent_high = high[-window:]
        recent_low = low[-window:]
        
        zones = []
        swing_threshold = 0.005 # 0.5% move to qualify as swing
        
        # Identify Swing Highs
        for i in range(5, len(recent_high) - 5):
            if recent_high[i] == max(recent_high[i-5:i+6]):
                price = recent_high[i]
                if not any(abs(z.price - price) < price * 0.01 for z in zones if z.type == 'resistance'):
                    zones.append(SRZone(
                        price=price,
                        strength=1.0,
                        type='resistance',
                        touches=1,
                        upper_bound=price * 1.002,
                        lower_bound=price
                    ))
                    
        # Identify Swing Lows
        for i in range(5, len(recent_low) - 5):
            if recent_low[i] == min(recent_low[i-5:i+6]):
                price = recent_low[i]
                if not any(abs(z.price - price) < price * 0.01 for z in zones if z.type == 'support'):
                    zones.append(SRZone(
                        price=price,
                        strength=1.0,
                        type='support',
                        touches=1,
                        upper_bound=price,
                        lower_bound=price * 0.998
                    ))
                    
        return zones

class ConfluenceEngine:
    """Core Logic: Intersect Shadow with S/R"""
    
    @staticmethod
    def calculate_confluence(current_price: float, shadow: FutureShadow, zones: List[SRZone]) -> List[ConfluenceSignal]:
        signals = []
        if not shadow or not zones:
            return signals
            
        # Check intersection of shadow path with zones
        for i, proj_price in enumerate(shadow.projected_prices):
            bars_to_impact = i + 1
            
            for zone in zones:
                # Check if projection enters the zone
                in_zone = zone.lower_bound <= proj_price <= zone.upper_bound
                
                if in_zone:
                    alignment_score = 0.0
                    signal_type = ""
                    
                    if shadow.direction == "bull" and zone.type == "support":
                        alignment_score = 1.0
                        signal_type = "bounce_long"
                    elif shadow.direction == "bear" and zone.type == "resistance":
                        alignment_score = 1.0
                        signal_type = "rejection_short"
                    elif shadow.direction == "bull" and zone.type == "resistance":
                        signal_type = "breakout_up"
                        alignment_score = 0.6
                    elif shadow.direction == "bear" and zone.type == "support":
                        signal_type = "breakout_down"
                        alignment_score = 0.6
                        
                    total_score = (shadow.confidence * 0.5) + (alignment_score * 0.5)
                    
                    if total_score > 0.4: 
                        signals.append(ConfluenceSignal(
                            timestamp=datetime.now(),
                            symbol="CALCULATED",
                            current_price=current_price,
                            signal_type=signal_type,
                            confluence_score=total_score,
                            sr_zone_price=zone.price,
                            sr_zone_type=zone.type,
                            shadow_direction=shadow.direction,
                            estimated_bars_to_impact=bars_to_impact,
                            projection_path=list(zip(shadow.bars_ahead, shadow.projected_prices))
                        ))
                        
        return signals

def run_confluence_analysis(df: pd.DataFrame, symbol: str) -> List[ConfluenceSignal]:
    """
    Main entry point for backend analysis.
    Input: DataFrame with OHLCV
    Output: List of ConfluenceSignal objects (JSON serializable via .to_dict())
    """
    if df.empty:
        return []
        
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values
    
    # 1. Find Pattern
    pattern = PatternEngine.find_best_analogue(close, np.arange(len(close)), window=50, lookback=500, tol=0.004)
    
    if not pattern:
        return []
        
    # 2. Generate Shadow
    shadow = PatternEngine.generate_future_shadow(close, pattern, window=50)
    
    # 3. Detect S/R
    zones = SRDetector.identify_zones(close, high, low, window=200)
    
    # 4. Find Confluence
    raw_signals = ConfluenceEngine.calculate_confluence(close[-1], shadow, zones)
    
    # Attach metadata
    for sig in raw_signals:
        sig.symbol = symbol
        
    return raw_signals
