# GIL - General Intelligence Layer

This project demonstrates how LLMs (Large Language Models) can serve as the "Intelligence Provider" for robotic systems, encapsulated as a Model Context Protocol (MCP) server.

## Architecture

The system is split into two independent MCP servers and a frontend:

1.  **gil_controls/** (Python):
    -   **Role**: The Body.
    -   Manages robot state, inverse kinematics, and sensor data.
    -   Runs a WebSocket server to communicate with the simulation frontend.
    -   Exposes tools: `move_arm`, `control_gripper`, `get_robot_state`, `get_latest_image`.

2.  **gil_models/** (Python):
    -   **Role**: The Brain.
    -   Manages heavy AI models (Cosmos, Gemini, LidarGen).
    -   Processes visual data into semantic understanding.
    -   Exposes tools: `analyze_scene`, `generate_point_cloud`.

3.  **gil_frontend/** (React):
    -   **Role**: The World.
    -   Visualizes the 3D simulation (Three.js).
    -   Connects to `gil_controls` via WebSocket to receive commands and send sensor data.

## Usage Flow

An Autonomous Agent (e.g., running in Claude Desktop or a script) orchestrates the system:

1.  Agent calls `gil_controls.get_latest_image()`.
2.  Agent passes image to `gil_models.analyze_scene(image)`.
3.  Agent decides on action based on analysis.
4.  Agent calls `gil_controls.move_arm(x, y, z)`.

## Setup

### 1. Controls MCP

```bash
cd gil_controls
pip install -r requirements.txt
python src/main.py
```

### 2. Models MCP

```bash
cd gil_models
pip install -r requirements.txt
export GOOGLE_API_KEY=your_key
python src/main.py
```

### 3. Frontend

```bash
cd gil_frontend
npm install
npm run dev
```
