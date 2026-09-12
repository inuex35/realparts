import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";
import { useStore } from "./store.js";

window.realparts = useStore;      // the store, for a test or a console to read
createRoot(document.getElementById("root")).render(<App />);
