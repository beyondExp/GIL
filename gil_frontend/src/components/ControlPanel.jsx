import React from 'react';

export default function ControlPanel({ onReset }) {
  return (
    <div className="control-panel panel" style={{
      padding: '20px',
      display: 'flex',
      flexDirection: 'column',
      gap: '15px',
      minWidth: '200px'
    }}>
      <h2 style={{ 
        margin: 0, 
        fontSize: '1.2rem', 
        color: 'var(--bbot-accent)', 
        textTransform: 'uppercase', 
        letterSpacing: '1px',
        borderBottom: '1px solid var(--bbot-border)',
        paddingBottom: '10px'
      }}>
        Controls
      </h2>
      
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <button onClick={onReset}>
          ⟳ Reset Scenario
        </button>
      </div>
      
      <div style={{ fontSize: '0.8rem', color: 'var(--bbot-text-dim)' }}>
        <p>Mode: Autonomous (MCP)</p>
      </div>
    </div>
  );
}
