import React, { useState, useRef } from 'react';
import RobotCanvas from './components/RobotCanvas';
import ControlPanel from './components/ControlPanel';

function App() {
  const [status, setStatus] = useState('Disconnected');
  const [robotState, setRobotState] = useState(null);
  const canvasRef = useRef(null);

  const handleReset = () => {
    if (canvasRef.current) {
      canvasRef.current.resetScene();
    }
  };
  
  return (
    <div style={{ width: '100vw', height: '100vh', position: 'relative', overflow: 'hidden' }}>
      <RobotCanvas ref={canvasRef} onStateChange={setRobotState} onStatusChange={setStatus} />
      
      {/* HUD Overlay */}
      <div style={{
        position: 'absolute',
        top: 20,
        left: 20,
        zIndex: 10,
        display: 'flex',
        flexDirection: 'column',
        gap: '20px',
        maxHeight: '90vh',
        overflowY: 'auto'
      }}>
        <div className="panel" style={{ padding: '20px', minWidth: '250px' }}>
          <h1 style={{ margin: '0 0 10px 0', fontSize: '1.5rem', fontWeight: 700, color: 'var(--bbot-text)' }}>
            GIL <span style={{ color: 'var(--bbot-accent)' }}>Robot</span>
          </h1>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '15px' }}>
            <div style={{ 
              width: '8px', height: '8px', borderRadius: '50%', 
              background: status === 'Connected' ? '#10b981' : '#ef4444',
              boxShadow: status === 'Connected' ? '0 0 8px #10b981' : 'none'
            }} />
            <span style={{ fontSize: '0.9rem', color: 'var(--bbot-text-dim)' }}>{status}</span>
          </div>
          
          {robotState && (
            <div style={{ fontSize: '0.8rem', fontFamily: 'monospace', color: 'var(--bbot-text)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Gripper:</span>
                <span style={{ 
                  fontWeight: 'bold',
                  color: robotState.gripper === 'OPEN' ? '#10b981' : '#f59e0b' 
                }}>
                  {robotState.gripper}
                </span>
              </div>
            </div>
          )}
        </div>

        <ControlPanel onReset={handleReset} />

        {/* Vision Streams */}
        {robotState && robotState.vision && (
          <div className="panel" style={{ padding: '15px', minWidth: '250px' }}>
             <h2 style={{ 
                margin: '0 0 10px 0', 
                fontSize: '1rem', 
                color: 'var(--bbot-text)',
                borderBottom: '1px solid var(--bbot-border)',
                paddingBottom: '5px'
              }}>
                Robot Vision
              </h2>
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                <div>
                  <div style={{ fontSize: '0.75rem', marginBottom: '4px', color: 'var(--bbot-text-dim)' }}>Left Eye</div>
                  <img src={robotState.vision.image_left} alt="Left Eye" style={{ width: '100%', borderRadius: '4px', border: '1px solid var(--bbot-border)' }} />
                </div>
                <div>
                  <div style={{ fontSize: '0.75rem', marginBottom: '4px', color: 'var(--bbot-text-dim)' }}>Right Eye</div>
                  <img src={robotState.vision.image_right} alt="Right Eye" style={{ width: '100%', borderRadius: '4px', border: '1px solid var(--bbot-border)' }} />
                </div>
              </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default App;
