// Both the initial cached menu and its asynchronous refresh use this contract.
export function sessionMenuResources(browserAvailable, resources) {
  return {
    browser: browserAvailable && resources && resources.browser ? resources.browser : null,
    files: resources && Array.isArray(resources.files) ? resources.files : [],
  };
}
