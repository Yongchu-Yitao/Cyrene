import { workbenchServices } from "../shared/runtime/services.jsx"

const FirstRunPage = React.lazy(() => import("../workbench-welcome.jsx"));

function OnboardingFlow(props) {
  const { t } = workbenchServices.i18n().use();
  return <React.Suspense fallback={<div className="workbench-empty" role="status">{t("workbench.welcomeLoading")}</div>}>
    <FirstRunPage {...props}/>
  </React.Suspense>;
}

window.CyreneUI.welcome = window.CyreneUI.register("welcome", { Page: OnboardingFlow });
