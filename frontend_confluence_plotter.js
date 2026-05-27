/**
 * MMXM Confluence Signal Plotter
 * Frontend component to plot confluence signals on charts.
 * Uses TradingView Lightweight Charts or similar library.
 * 
 * BACKEND sends data via API, FRONTEND handles all visualization.
 */

// Configuration matching the backend signal types
const SIGNAL_CONFIGS = {
    bounce_long: {
        color: '#00ff00', // Green
        shape: 'triangleUp',
        text: 'Bounce Long',
        tooltip: 'Future Shadow hits Support with Bullish Analogue'
    },
    rejection_short: {
        color: '#ff0000', // Red
        shape: 'triangleDown',
        text: 'Rejection Short',
        tooltip: 'Future Shadow hits Resistance with Bearish Analogue'
    },
    breakout_up: {
        color: '#ffa500', // Orange
        shape: 'circle',
        text: 'Breakout Up',
        tooltip: 'Bullish Shadow breaking Resistance'
    },
    breakout_down: {
        color: '#800080', // Purple
        shape: 'circle',
        text: 'Breakout Down',
        tooltip: 'Bearish Shadow breaking Support'
    }
};

/**
 * Fetch confluence signals from backend API
 */
async function fetchConfluenceSignals(symbols = ['BTCUSD', 'ETHUSD']) {
    try {
        const symbolParam = symbols.join(',');
        const response = await fetch(`/api/confluence/signals?symbols=${symbolParam}`);
        
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch confluence signals:', error);
        return [];
    }
}

/**
 * Fetch high-priority alerts only (for warnings/popups)
 */
async function fetchConfluenceAlerts(symbols = ['BTCUSD', 'ETHUSD'], minScore = 0.6) {
    try {
        const symbolParam = symbols.join(',');
        const response = await fetch(
            `/api/confluence/alerts?symbols=${symbolParam}&min_score=${minScore}`
        );
        
        if (!response.ok) {
            throw new Error(`API error: ${response.status}`);
        }
        
        return await response.json();
    } catch (error) {
        console.error('Failed to fetch alerts:', error);
        return [];
    }
}

/**
 * Plot confluence markers on TradingView Lightweight Chart
 * @param {Object} chart - TradingView chart instance
 * @param {Array} signals - Array of signal objects from API
 */
function plotConfluenceSignals(chart, signals) {
    // Create marker series for signals
    const markers = [];
    
    signals.forEach(signal => {
        const config = SIGNAL_CONFIGS[signal.signal_type];
        if (!config) {
            console.warn(`Unknown signal type: ${signal.signal_type}`);
            return;
        }
        
        // Convert timestamp to chart time format
        const chartTime = new Date(signal.timestamp).getTime() / 1000;
        
        markers.push({
            time: chartTime,
            position: signal.signal_type.includes('long') || signal.signal_type.includes('up') ? 'belowBar' : 'aboveBar',
            color: config.color,
            shape: config.shape,
            text: `${config.text} (${signal.confluence_score})`,
            tooltip: `${config.tooltip}\nSR Level: ${signal.sr_zone_price}\nImpact: ${signal.estimated_bars_to_impact} bars`
        });
    });
    
    // Apply markers to the main series
    const candleSeries = chart.series().get(0); // Assuming first series is candles
    candleSeries.setMarkers(markers);
    
    console.log(`Plotted ${markers.length} confluence signals`);
}

/**
 * Draw Future Shadow projection path on chart
 * @param {Object} chart - TradingView chart instance
 * @param {Array} signals - Array of signal objects with projection_path
 */
function drawShadowProjections(chart, signals) {
    signals.forEach(signal => {
        if (!signal.projection_path || signal.projection_path.length === 0) return;
        
        const config = SIGNAL_CONFIGS[signal.signal_type];
        const lastBarTime = chart.timeScale().getVisibleLogicalRange();
        
        // Create line series for shadow path
        const shadowLine = chart.addLineSeries({
            color: config.color,
            lineWidth: 2,
            lineStyle: 2, // Dashed line
            crosshairMarkerVisible: false,
            lastValueVisible: false,
            priceLineVisible: false
        });
        
        // Map projection path to chart data points
        const shadowData = signal.projection_path.map(point => ({
            time: (new Date().getTime() / 1000) + (point.bar * 3600), // Assuming 1h bars
            value: point.price
        }));
        
        shadowLine.setData(shadowData);
        
        // Store reference to clean up later if needed
        window.shadowLines = window.shadowLines || [];
        window.shadowLines.push(shadowLine);
    });
}

/**
 * Draw horizontal S/R zones at impact levels
 * @param {Object} chart - TradingView chart instance
 * @param {Array} signals - Array of signal objects
 */
function drawSRZones(chart, signals) {
    const processedPrices = new Set(); // Avoid duplicate lines
    
    signals.forEach(signal => {
        const price = signal.sr_zone_price;
        if (processedPrices.has(price)) return;
        processedPrices.add(price);
        
        const config = SIGNAL_CONFIGS[signal.signal_type];
        const zoneColor = signal.sr_zone_type === 'support' ? 'rgba(0, 255, 0, 0.3)' : 'rgba(255, 0, 0, 0.3)';
        
        // Add horizontal line using chart's price line feature
        // Note: Lightweight Charts doesn't have native horizontal lines, 
        // you may need to use a separate library or custom canvas overlay
        console.log(`Drawing ${signal.sr_zone_type} zone at ${price}`);
        
        // Alternative: Use a thin area series to represent the zone
        const zoneArea = chart.addAreaSeries({
            topColor: zoneColor,
            bottomColor: zoneColor,
            lineWidth: 0,
            crosshairMarkerVisible: false,
            lastValueVisible: false,
            priceLineVisible: false
        });
        
        // Create a flat area at the S/R level across visible range
        const visibleRange = chart.timeScale().getVisibleLogicalRange();
        if (visibleRange) {
            const zoneData = [
                { time: (new Date().getTime() / 1000) - 86400, value: price }, // 1 day ago
                { time: (new Date().getTime() / 1000) + 86400, value: price }  // 1 day ahead
            ];
            zoneArea.setData(zoneData);
            
            window.srZones = window.srZones || [];
            window.srZones.push(zoneArea);
        }
    });
}

/**
 * Show alert popup for high-priority confluence
 * @param {Array} alerts - Array of high-score alerts
 */
function showConfluenceAlerts(alerts) {
    if (alerts.length === 0) return;
    
    alerts.forEach(alert => {
        const config = SIGNAL_CONFIGS[alert.signal_type];
        
        // Create notification element (customize based on your UI framework)
        const notification = document.createElement('div');
        notification.className = 'confluence-alert';
        notification.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: ${config.color};
            color: white;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 10px;
            z-index: 9999;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            animation: slideIn 0.3s ease-out;
        `;
        
        notification.innerHTML = `
            <strong>⚠️ CONFLUENCE ALERT</strong><br>
            ${alert.symbol}: ${config.text}<br>
            Score: ${alert.confluence_score} | Level: ${alert.sr_zone_price}<br>
            Impact in ~${alert.estimated_bars_to_impact} bars
        `;
        
        document.body.appendChild(notification);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            notification.style.animation = 'slideOut 0.3s ease-out';
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    });
}

/**
 * Initialize confluence monitoring for current chart
 * Call this when chart loads or symbol changes
 */
async function initializeConfluenceMonitor(symbol, chart) {
    console.log(`Initializing confluence monitor for ${symbol}`);
    
    // Fetch and plot signals
    const signals = await fetchConfluenceSignals([symbol]);
    if (signals.length > 0) {
        plotConfluenceSignals(chart, signals);
        drawShadowProjections(chart, signals);
        drawSRZones(chart, signals);
    }
    
    // Check for urgent alerts
    const alerts = await fetchConfluenceAlerts([symbol], 0.7);
    if (alerts.length > 0) {
        showConfluenceAlerts(alerts);
    }
    
    // Set up periodic refresh (every 5 minutes)
    setInterval(async () => {
        const freshSignals = await fetchConfluenceSignals([symbol]);
        // Clear old markers/lines before redrawing
        if (window.shadowLines) window.shadowLines.forEach(line => chart.removeSeries(line));
        if (window.srZones) window.srZones.forEach(zone => chart.removeSeries(zone));
        
        if (freshSignals.length > 0) {
            plotConfluenceSignals(chart, freshSignals);
            drawShadowProjections(chart, freshSignals);
            drawSRZones(chart, freshSignals);
        }
    }, 300000); // 5 minutes
}

// Export for use in your frontend framework
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        fetchConfluenceSignals,
        fetchConfluenceAlerts,
        plotConfluenceSignals,
        drawShadowProjections,
        drawSRZones,
        showConfluenceAlerts,
        initializeConfluenceMonitor
    };
}
