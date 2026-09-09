/*
 * JNI bridge for the GPL-2.0 Limbo/QEMU Android engine. The native ABI uses
 * this historical class name, so keep the package and method signatures
 * stable even though Cyrene does not use Limbo's UI.
 */
package com.max2idea.android.limbo.jni;

public final class VMExecutor {
    static {
        System.loadLibrary("compat-musl");
        System.loadLibrary("compat-limbo");
        System.loadLibrary("glib-2.0");
        System.loadLibrary("pixman-1");
        System.loadLibrary("SDL2");
        System.loadLibrary("compat-SDL2-ext");
        System.loadLibrary("compat-SDL2-addons");
        System.loadLibrary("limbo");
    }

    public native String start(
            String storageDir,
            String baseDir,
            String libraryName,
            String libraryPath,
            int sdlScaleHint,
            Object[] parameters
    );

    public native String stop(int restart);

    public int get_fd(String path) {
        return -1;
    }

    public int close_fd(int fd) {
        return -1;
    }

    public static void onVMResolutionChanged(int width, int height) {
        // Cyrene is headless; QEMU never creates a display surface.
    }
}
