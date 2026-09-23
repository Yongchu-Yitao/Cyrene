// The Workbench shell loads React's UMD runtime before the ESM application.
// Third-party ESM components must share its dispatcher and contexts.
const React = window.React;
export default React;
export const { Children, Component, Fragment, Profiler, PureComponent, StrictMode,
  Suspense, cloneElement, createContext, createElement, createFactory, createRef,
  forwardRef, isValidElement, lazy, memo, startTransition, useCallback, useContext,
  useDebugValue, useDeferredValue, useEffect, useId, useImperativeHandle,
  useInsertionEffect, useLayoutEffect, useMemo, useReducer, useRef, useState,
  useSyncExternalStore, useTransition, version,
  __SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED } = React;
