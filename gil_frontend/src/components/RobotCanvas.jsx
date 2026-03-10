import React, { useRef, useEffect, useImperativeHandle, forwardRef } from 'react';
import { RobotScene } from '../logic/RobotScene';

const RobotCanvas = forwardRef(({ onStateChange, onStatusChange }, ref) => {
  const containerRef = useRef(null);
  const sceneRef = useRef(null);

  useImperativeHandle(ref, () => ({
    resetScene: () => {
      if (sceneRef.current) {
        sceneRef.current.reset();
      }
    }
  }));

  useEffect(() => {
    // Cleanup previous instance if exists (Double-Render in Strict Mode prevention)
    if (sceneRef.current) {
      sceneRef.current.dispose();
      sceneRef.current = null;
    }

    if (containerRef.current) {
      sceneRef.current = new RobotScene(containerRef.current, onStateChange, onStatusChange);
    }

    return () => {
      if (sceneRef.current) {
        sceneRef.current.dispose();
        sceneRef.current = null; // Ensure reference is cleared
      }
    };
  }, []); // Empty dependency array means this runs once on mount

  return (
    <div 
      ref={containerRef} 
      style={{ width: '100%', height: '100%' }}
    />
  );
});

export default RobotCanvas;

