// 窗口探针：列出当前所有窗口，并标出「是不是真的在屏幕上」。
//
// 为什么需要它（2026-10-02 加）：验证 summon 面板是否可见、几何是否正确，
// 此前只能靠截图（本会话的主模型还不收图）或 Playwright（只能看页面，看不到原生窗口）。
// 这个 50 行的 C 程序直接问 WindowServer，给出 pid / 层级 / 尺寸 / 位置 / onscreen / alpha，
// 于是「面板到底在不在屏幕上」变成一条可断言的事实。
//
// 编译（本机 Command Line Tools 自带 clang，不需要 Xcode）：
//   clang -O2 -framework CoreGraphics -framework CoreFoundation scripts/winlist.c -o /tmp/winlist
// 用法：
//   /tmp/winlist                      # 只列**在屏上**的窗口
//   /tmp/winlist --all                # 列所有窗口（含隐藏 / 其它 Space）
//   /tmp/winlist --all | grep 铭荼     # 看我们自己的窗口
//
// 判读要点：
//   - summon 宠物档 = `layer=5`、约 `220x220`、标题「铭荼助手」、右下角附近；
//   - `onscreen=0` 表示窗口存在但**不在当前屏幕上**（被隐藏、或在另一个 Space）——
//     这正好是「日志说面板已显示、用户却说没看见」的常见成因；
//   - 窗口**标题**需要屏幕录制权限，没有权限时 name 为空（其余字段不需要权限）。

#include <CoreGraphics/CoreGraphics.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    int all = (argc > 1 && strcmp(argv[1], "--all") == 0);
    CGWindowListOption opts = all ? kCGWindowListOptionAll
                                  : (kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements);
    CFArrayRef list = CGWindowListCopyWindowInfo(opts, kCGNullWindowID);
    if (list == NULL) {
        fprintf(stderr, "no window list\n");
        return 1;
    }
    CFIndex n = CFArrayGetCount(list);
    for (CFIndex i = 0; i < n; i++) {
        CFDictionaryRef w = CFArrayGetValueAtIndex(list, i);
        CFNumberRef pid = CFDictionaryGetValue(w, kCGWindowOwnerPID);
        CFNumberRef layer = CFDictionaryGetValue(w, kCGWindowLayer);
        CFNumberRef alpha = CFDictionaryGetValue(w, kCGWindowAlpha);
        CFBooleanRef onscreen = CFDictionaryGetValue(w, kCGWindowIsOnscreen);
        CFStringRef owner = CFDictionaryGetValue(w, kCGWindowOwnerName);
        CFStringRef name = CFDictionaryGetValue(w, kCGWindowName);
        CFDictionaryRef bounds = CFDictionaryGetValue(w, kCGWindowBounds);

        int pidv = -1, layerv = -1, onv = 0;
        double alphav = -1;
        if (pid) CFNumberGetValue(pid, kCFNumberIntType, &pidv);
        if (layer) CFNumberGetValue(layer, kCFNumberIntType, &layerv);
        if (alpha) CFNumberGetValue(alpha, kCFNumberDoubleType, &alphav);
        if (onscreen) onv = CFBooleanGetValue(onscreen) ? 1 : 0;

        CGRect r = CGRectNull;
        if (bounds) CGRectMakeWithDictionaryRepresentation(bounds, &r);

        char ownerbuf[256] = "?", namebuf[256] = "";
        if (owner) CFStringGetCString(owner, ownerbuf, sizeof ownerbuf, kCFStringEncodingUTF8);
        if (name) CFStringGetCString(name, namebuf, sizeof namebuf, kCFStringEncodingUTF8);

        printf("pid=%-6d layer=%-3d onscreen=%d alpha=%.2f %4.0fx%-4.0f @(%.0f,%.0f)  %s%s%s\n",
               pidv, layerv, onv, alphav, r.size.width, r.size.height, r.origin.x, r.origin.y,
               ownerbuf, namebuf[0] ? " | " : "", namebuf);
    }
    CFRelease(list);
    return 0;
}
