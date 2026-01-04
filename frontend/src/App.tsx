import { Navigate, Route, Routes } from "react-router-dom";
import DashboardLayout from "./layouts/DashboardLayout";
import CoachDashboard from "./pages/CoachDashboard";
import PatientDashboard from "./pages/PatientDashboard";

const App = () => {
  return (
    <Routes>
      <Route element={<DashboardLayout />}>
        <Route path="/coach" element={<CoachDashboard />} />
        <Route path="/patient" element={<PatientDashboard />} />
        <Route path="*" element={<Navigate to="/coach" replace />} />
      </Route>
    </Routes>
  );
};

export default App;
