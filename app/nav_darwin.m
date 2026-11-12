// Wails v3.1.0's webview delegate (WebviewWindowDelegate) has no navigation policy and no new-window handler, so
// the app adds both to its class before the first window exists: WebKit asks the delegate which methods it has when
// a webview gets it. It also shuts Wails' wails:// scheme, which the app never loads: behind it is Wails' runtime
// (browser, clipboard, dialogs), with no origin check of its own.
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#include "nav_darwin.h"
#include "_cgo_export.h"

static int ask(WKNavigationAction *action, int newWindow) {
	NSString *url = action.request.URL.absoluteString;
	WKFrameInfo *source = action.sourceFrame;
	return navDecide((char *)(url ? url.UTF8String : ""), newWindow,
	                 action.navigationType == WKNavigationTypeLinkActivated, source != nil && source.isMainFrame);
}

static void decidePolicy(id self, SEL cmd, WKWebView *webView, WKNavigationAction *action,
                         void (^decisionHandler)(WKNavigationActionPolicy)) {
	int what = ask(action, action.targetFrame == nil);
	decisionHandler(what == NAV_ALLOW ? WKNavigationActionPolicyAllow : WKNavigationActionPolicyCancel);
}

// window.open and target=_blank when WebKit did not ask the policy first: never a new webview from WebKit.
static WKWebView *createWebView(id self, SEL cmd, WKWebView *webView, WKWebViewConfiguration *config,
                                WKNavigationAction *action, WKWindowFeatures *features) {
	ask(action, 1);
	return nil;
}

static void refuseSchemeTask(id self, SEL cmd, WKWebView *webView, id<WKURLSchemeTask> task) {
	[task didFailWithError:[NSError errorWithDomain:NSURLErrorDomain code:NSURLErrorUnsupportedURL userInfo:nil]];
}

static int installed = 0;

int installNavigationPolicy(void) {
	if (installed) {
		return 1;
	}
	Class delegate = objc_getClass("WebviewWindowDelegate");
	if (delegate == Nil) {
		return 0;
	}
	// WebKit prefers this variant over the one added here: a Wails that has it would bypass the policy.
	if (class_respondsToSelector(delegate, @selector(webView:decidePolicyForNavigationAction:preferences:decisionHandler:))) {
		return 0;
	}
	// class_addMethod refuses a selector the class already has: a Wails that grows its own policy fails here.
	if (!class_addMethod(delegate, @selector(webView:decidePolicyForNavigationAction:decisionHandler:),
	                     (IMP)decidePolicy, "v@:@@@?") ||
	    !class_addMethod(delegate,
	                     @selector(webView:createWebViewWithConfiguration:forNavigationAction:windowFeatures:),
	                     (IMP)createWebView, "@@:@@@@")) {
		return 0;
	}
	// Replaces Wails' own handler; nil means Wails had none, so its scheme is set up some other way now.
	if (class_replaceMethod(delegate, @selector(webView:startURLSchemeTask:), (IMP)refuseSchemeTask, "v@:@@") == NULL) {
		return 0;
	}
	installed = 1;
	return 1;
}
