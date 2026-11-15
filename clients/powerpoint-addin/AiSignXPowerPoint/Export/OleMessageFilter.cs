using System;
using System.Runtime.InteropServices;

namespace AiSignXPowerPoint.Export
{
    /// <summary>
    /// COM message filter that retries calls PowerPoint rejects while it is busy (for example
    /// while it renders a video) instead of failing with RPC_E_SERVERCALL_RETRYLATER.
    ///
    ///     using (OleMessageFilter.Register()) { ...COM calls... }
    ///
    /// Dispose puts back whatever filter was registered before, so PowerPoint's own filter on
    /// its UI thread is restored. On a thread that cannot have a filter (an MTA thread) this
    /// is a no-op.
    /// </summary>
    internal sealed class OleMessageFilter : IOleMessageFilter, IDisposable
    {
        private const int ServerCallIsHandled = 0;
        private const int ServerCallRetryLater = 2;
        private const int PendingMsgWaitDefProcess = 2;
        private static readonly int RetryWindowMs = 120000;

        private readonly IOleMessageFilter _previous;
        private readonly bool _registered;
        private readonly int _threadId;
        private bool _disposed;

        private OleMessageFilter(bool register)
        {
            _threadId = Environment.CurrentManagedThreadId;
            if (!register)
                return;
            int hr = CoRegisterMessageFilter(this, out _previous);
            _registered = hr >= 0;
        }

        public static IDisposable Register() => new OleMessageFilter(register: true);

        public void Dispose()
        {
            // A filter belongs to one thread; never restore it from another one.
            if (_disposed || !_registered || Environment.CurrentManagedThreadId != _threadId)
                return;
            _disposed = true;
            CoRegisterMessageFilter(_previous, out IOleMessageFilter _);
        }

        int IOleMessageFilter.HandleInComingCall(int dwCallType, IntPtr hTaskCaller, int dwTickCount,
                                                 IntPtr lpInterfaceInfo)
        {
            return ServerCallIsHandled;
        }

        int IOleMessageFilter.RetryRejectedCall(IntPtr hTaskCallee, int dwTickCount, int dwRejectType)
        {
            // Retry "busy" rejections every 100 ms for up to two minutes; cancel anything else.
            if (dwRejectType == ServerCallRetryLater && dwTickCount < RetryWindowMs)
                return 100;
            return -1;
        }

        int IOleMessageFilter.MessagePending(IntPtr hTaskCallee, int dwTickCount, int dwPendingType)
        {
            return PendingMsgWaitDefProcess;
        }

        [DllImport("ole32.dll")]
        private static extern int CoRegisterMessageFilter(IOleMessageFilter newFilter,
                                                          out IOleMessageFilter oldFilter);
    }

    [ComImport, Guid("00000016-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IOleMessageFilter
    {
        [PreserveSig]
        int HandleInComingCall(int dwCallType, IntPtr hTaskCaller, int dwTickCount, IntPtr lpInterfaceInfo);

        [PreserveSig]
        int RetryRejectedCall(IntPtr hTaskCallee, int dwTickCount, int dwRejectType);

        [PreserveSig]
        int MessagePending(IntPtr hTaskCallee, int dwTickCount, int dwPendingType);
    }
}
