package com.voiceaipoc.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.util.Log
import java.util.concurrent.atomic.AtomicBoolean

/** Shared, reference-counted owner for the Android duplex communication route. */
class AudioRouteController internal constructor(
    private val platform: Platform,
    private val config: Config = Config(),
) {
    constructor(context: Context, config: Config = Config()) : this(
        AndroidAudioRoutePlatform(context.applicationContext), config,
    )

    enum class Owner { CAPTURE, PLAYBACK }
    enum class DevicePreference { AUTO, SPEAKER, EARPIECE, WIRED, BLUETOOTH, BLE }

    data class Config(
        val devicePreference: DevicePreference = DevicePreference.AUTO,
        val requestAudioFocus: Boolean = true,
        /** When false, leases are ownership-only and Android mode/focus/device stay untouched. */
        val communicationRouteEnabled: Boolean = true,
    )

    data class AcquireResult(
        val succeeded: Boolean,
        val lease: Lease? = null,
        val errorCode: String? = null,
        val errorMessage: String? = null,
    )

    interface Lease {
        val owner: Owner
        fun release()
    }

    interface FocusLossListener {
        fun onAudioFocusLost(change: Int)
    }

    interface FocusListenerRegistration { fun unregister() }

    data class CommunicationDevice(val type: Int, val name: String)

    data class Status(
        val communicationRouteEnabled: Boolean,
        val activeLeaseCount: Int,
        val captureLeaseCount: Int,
        val playbackLeaseCount: Int,
        val requestedMode: Int,
        val actualMode: Int,
        val priorMode: Int?,
        val modeAcquired: Boolean,
        val modeRestored: Boolean,
        val requestedCommunicationDevice: String,
        val actualCommunicationDevice: String,
        val inputDeviceType: String,
        val outputDeviceType: String,
        val communicationDeviceSelected: Boolean,
        val playbackRoute: String,
        val audioFocusRequested: Boolean,
        val audioFocusGranted: Boolean,
        val audioFocusState: String,
        val audioFocusRestored: Boolean,
        val restorationCount: Int,
        val api31CommunicationDeviceSupported: Boolean,
        val lastError: String?,
    )

    companion object {
        const val MODE_IN_COMMUNICATION = AudioManager.MODE_IN_COMMUNICATION
        const val AUDIO_FOCUS_GRANTED = AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        private const val TAG = "VoiceAI-Route"
    }

    private val lock = Any()
    private val leases = linkedMapOf<Long, Owner>()
    private val focusListeners = linkedMapOf<Long, FocusLossListener>()
    private var nextId = 0L
    private var priorMode: Int? = null
    private var priorSpeakerphoneOn: Boolean? = null
    private var selectedDevice: CommunicationDevice? = null
    private var requestedDevice = DevicePreference.AUTO.name
    private var focusRequested = false
    private var focusGranted = false
    private var focusState = "NONE"
    private var focusRestored = true
    private var modeAcquired = false
    private var modeRestored = true
    private var restorationCount = 0
    private var lastError: String? = null
    private var bluetoothScoStarted = false
    private var speakerphoneChanged = false

    private val focusChangeListener: (Int) -> Unit = { change ->
        val listeners = synchronized(lock) {
            focusState = when (change) {
                AudioManager.AUDIOFOCUS_LOSS -> "LOST"
                AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> "LOST_TRANSIENT"
                AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> "LOST_CAN_DUCK"
                AudioManager.AUDIOFOCUS_GAIN -> "GRANTED"
                else -> "CHANGE_$change"
            }
            focusGranted = change == AudioManager.AUDIOFOCUS_GAIN
            if (change == AudioManager.AUDIOFOCUS_LOSS ||
                change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT ||
                change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK
            ) focusListeners.values.toList() else emptyList()
        }
        listeners.forEach { listener ->
            runCatching { listener.onAudioFocusLost(change) }
                .onFailure { Log.w(TAG, "Focus-loss listener failed", it) }
        }
    }

    fun acquire(owner: Owner): AcquireResult = synchronized(lock) {
        if (leases.isEmpty()) {
            val result = if (config.communicationRouteEnabled) {
                acquirePlatformRouteLocked()
            } else {
                requestedDevice = "DISABLED_BY_POLICY"
                focusRequested = false
                focusGranted = false
                focusState = "DISABLED_BY_POLICY"
                focusRestored = true
                modeAcquired = false
                modeRestored = true
                lastError = null
                AcquireResult(true)
            }
            if (!result.succeeded) {
                restorePlatformRouteLocked()
                return@synchronized result
            }
        }
        nextId += 1
        leases[nextId] = owner
        modeRestored = false
        AcquireResult(true, ControllerLease(nextId, owner))
    }

    fun registerFocusLossListener(listener: FocusLossListener): FocusListenerRegistration =
        synchronized(lock) {
            nextId += 1
            focusListeners[nextId] = listener
            FocusRegistration(nextId)
        }

    fun getStatus(): Status = synchronized(lock) {
        Status(
            communicationRouteEnabled = config.communicationRouteEnabled,
            activeLeaseCount = leases.size,
            captureLeaseCount = leases.values.count { it == Owner.CAPTURE },
            playbackLeaseCount = leases.values.count { it == Owner.PLAYBACK },
            requestedMode = MODE_IN_COMMUNICATION,
            actualMode = platform.currentMode(),
            priorMode = priorMode,
            modeAcquired = modeAcquired,
            modeRestored = modeRestored,
            requestedCommunicationDevice = requestedDevice,
            actualCommunicationDevice = selectedDevice?.let(::describeDevice)
                ?: platform.currentCommunicationDevice()?.let(::describeDevice) ?: "NONE",
            inputDeviceType = platform.describeInputDeviceType(),
            outputDeviceType = platform.describeOutputDeviceType(),
            communicationDeviceSelected = selectedDevice != null,
            playbackRoute = platform.describePlaybackRoute(),
            audioFocusRequested = focusRequested,
            audioFocusGranted = focusGranted,
            audioFocusState = focusState,
            audioFocusRestored = focusRestored,
            restorationCount = restorationCount,
            api31CommunicationDeviceSupported = platform.sdkInt >= Build.VERSION_CODES.S,
            lastError = lastError,
        )
    }

    private fun acquirePlatformRouteLocked(): AcquireResult {
        priorMode = platform.currentMode()
        priorSpeakerphoneOn = platform.isSpeakerphoneOn()
        requestedDevice = config.devicePreference.name
        modeRestored = false
        focusRestored = !config.requestAudioFocus
        lastError = null
        return try {
            platform.setMode(MODE_IN_COMMUNICATION)
            modeAcquired = true
            selectDeviceLocked()
            if (config.requestAudioFocus) {
                focusRequested = true
                val result = platform.requestAudioFocus(focusChangeListener)
                focusGranted = result == AUDIO_FOCUS_GRANTED
                focusState = if (focusGranted) "GRANTED" else "FAILED_$result"
                if (!focusGranted) {
                    return failAcquireLocked(
                        "E_AUDIO_FOCUS",
                        "Communication audio focus was not granted: result=$result",
                    )
                }
                focusRestored = false
            }
            Log.i(TAG, "ROUTE_ACQUIRED mode=${platform.currentMode()} focus=$focusState")
            AcquireResult(true)
        } catch (exception: RuntimeException) {
            failAcquireLocked("E_AUDIO_ROUTE", "Communication route failed: ${exception.message}")
        }
    }

    private fun selectDeviceLocked() {
        if (platform.sdkInt >= Build.VERSION_CODES.S) {
            val device = chooseDevice(platform.availableCommunicationDevices(), config.devicePreference)
            if (device == null) {
                lastError = "No communication device matched ${config.devicePreference.name}; using fallback"
            } else if (platform.currentCommunicationDevice()?.type == device.type ||
                platform.setCommunicationDevice(device)
            ) {
                selectedDevice = device
            } else {
                lastError = "Communication device selection failed for ${describeDevice(device)}"
            }
            applySpeakerphoneLocked()
            return
        }
        when (config.devicePreference) {
            DevicePreference.SPEAKER -> applySpeakerphoneLocked()
            DevicePreference.BLUETOOTH, DevicePreference.BLE -> {
                bluetoothScoStarted = platform.startBluetoothSco()
                if (!bluetoothScoStarted) lastError = "Bluetooth route unavailable; using fallback"
            }
            DevicePreference.AUTO, DevicePreference.EARPIECE, DevicePreference.WIRED -> Unit
        }
    }

    private fun applySpeakerphoneLocked() {
        if (config.devicePreference != DevicePreference.SPEAKER) return
        // ColorOS and other OEM builds often ignore setCommunicationDevice unless
        // speakerphone is also forced on, including API 31+.
        platform.setSpeakerphoneOn(true)
        speakerphoneChanged = true
    }

    private fun chooseDevice(
        devices: List<CommunicationDevice>, preference: DevicePreference,
    ): CommunicationDevice? {
        fun first(vararg types: Int): CommunicationDevice? {
            types.forEach { type ->
                devices.firstOrNull { it.type == type }?.let { return it }
            }
            return null
        }
        return when (preference) {
            DevicePreference.SPEAKER -> first(
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
            )
            DevicePreference.EARPIECE -> first(
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
            )
            DevicePreference.WIRED -> first(
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_SCO,
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE, AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            )
            DevicePreference.BLUETOOTH -> first(
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BLE_HEADSET,
                AudioDeviceInfo.TYPE_BLE_SPEAKER, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            )
            DevicePreference.BLE -> first(
                AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER,
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            )
            DevicePreference.AUTO -> first(
                AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_WIRED_HEADPHONES,
                AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLE_SPEAKER,
                AudioDeviceInfo.TYPE_BLUETOOTH_SCO, AudioDeviceInfo.TYPE_BUILTIN_EARPIECE,
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER,
            )
        }
    }

    private fun failAcquireLocked(code: String, message: String): AcquireResult {
        lastError = "$code: $message"
        Log.e(TAG, lastError!!)
        return AcquireResult(false, errorCode = code, errorMessage = message)
    }

    private fun releaseLease(id: Long) = synchronized(lock) {
        if (leases.remove(id) != null && leases.isEmpty()) restorePlatformRouteLocked()
    }

    private fun unregisterFocusListener(id: Long) = synchronized(lock) {
        focusListeners.remove(id)
    }

    private fun restorePlatformRouteLocked() {
        if (modeRestored && focusRestored && !modeAcquired && selectedDevice == null) return
        val errors = mutableListOf<String>()
        if (platform.sdkInt >= Build.VERSION_CODES.S) {
            runCatching { platform.clearCommunicationDevice() }
                .onFailure { errors += "clearCommunicationDevice: ${it.message}" }
        }
        if (bluetoothScoStarted) {
            runCatching { platform.stopBluetoothSco() }
                .onFailure { errors += "stopBluetoothSco: ${it.message}" }
            bluetoothScoStarted = false
        }
        if (speakerphoneChanged && priorSpeakerphoneOn != null) {
            runCatching { platform.setSpeakerphoneOn(priorSpeakerphoneOn == true) }
                .onFailure { errors += "restoreSpeakerphone: ${it.message}" }
            speakerphoneChanged = false
        }
        if (focusRequested && !focusRestored) {
            runCatching { platform.abandonAudioFocus(focusChangeListener) }
                .onFailure { errors += "abandonAudioFocus: ${it.message}" }
            focusRequested = false
            focusGranted = false
            focusRestored = true
            focusState = "NONE"
        }
        priorMode?.let { saved ->
            runCatching { platform.setMode(saved) }
                .onFailure { errors += "restoreMode: ${it.message}" }
        }
        selectedDevice = null
        modeAcquired = false
        modeRestored = errors.isEmpty()
        restorationCount += 1
        lastError = errors.takeIf { it.isNotEmpty() }?.joinToString("; ")
        Log.i(TAG, "ROUTE_RESTORED mode=${platform.currentMode()} count=$restorationCount")
    }

    private fun describeDevice(device: CommunicationDevice): String =
        "${deviceTypeName(device.type)}:${device.name.ifBlank { "UNNAMED" }}"

    private fun deviceTypeName(type: Int): String = audioDeviceTypeName(type)

    private inner class ControllerLease(
        private val id: Long,
        override val owner: Owner,
    ) : Lease {
        private val released = AtomicBoolean(false)
        override fun release() { if (released.compareAndSet(false, true)) releaseLease(id) }
    }

    private inner class FocusRegistration(private val id: Long) : FocusListenerRegistration {
        private val unregistered = AtomicBoolean(false)
        override fun unregister() {
            if (unregistered.compareAndSet(false, true)) unregisterFocusListener(id)
        }
    }

    /** Android-independent seam used by JVM tests. */
    internal interface Platform {
        val sdkInt: Int
        fun currentMode(): Int
        fun setMode(mode: Int)
        fun requestAudioFocus(listener: (Int) -> Unit): Int
        fun abandonAudioFocus(listener: (Int) -> Unit): Int
        fun availableCommunicationDevices(): List<CommunicationDevice>
        fun currentCommunicationDevice(): CommunicationDevice?
        fun setCommunicationDevice(device: CommunicationDevice): Boolean
        fun clearCommunicationDevice()
        fun isSpeakerphoneOn(): Boolean
        fun setSpeakerphoneOn(enabled: Boolean)
        fun startBluetoothSco(): Boolean
        fun stopBluetoothSco()
        fun describePlaybackRoute(): String
        fun describeInputDeviceType(): String = "UNKNOWN"
        fun describeOutputDeviceType(): String = "UNKNOWN"
    }
}

private class AndroidAudioRoutePlatform(context: Context) : AudioRouteController.Platform {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null
    private var legacyFocusListener: AudioManager.OnAudioFocusChangeListener? = null
    override val sdkInt: Int = Build.VERSION.SDK_INT
    override fun currentMode(): Int = audioManager.mode
    override fun setMode(mode: Int) { audioManager.mode = mode }

    override fun requestAudioFocus(listener: (Int) -> Unit): Int {
        val callback = AudioManager.OnAudioFocusChangeListener { listener(it) }
        return if (sdkInt >= Build.VERSION_CODES.O) {
            val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT)
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setOnAudioFocusChangeListener(callback)
                .setWillPauseWhenDucked(true)
                .build()
            focusRequest = request
            audioManager.requestAudioFocus(request)
        } else {
            legacyFocusListener = callback
            audioManager.requestAudioFocus(
                callback, AudioManager.STREAM_VOICE_CALL, AudioManager.AUDIOFOCUS_GAIN_TRANSIENT,
            )
        }
    }

    override fun abandonAudioFocus(listener: (Int) -> Unit): Int {
        val request = focusRequest
        focusRequest = null
        return if (sdkInt >= Build.VERSION_CODES.O && request != null) {
            audioManager.abandonAudioFocusRequest(request)
        } else {
            legacyFocusListener?.let(audioManager::abandonAudioFocus)
                ?: AudioManager.AUDIOFOCUS_REQUEST_GRANTED
        }
    }

    override fun availableCommunicationDevices(): List<AudioRouteController.CommunicationDevice> =
        if (sdkInt >= Build.VERSION_CODES.S) audioManager.availableCommunicationDevices.map {
            AudioRouteController.CommunicationDevice(it.type, it.productName?.toString().orEmpty())
        } else emptyList()

    override fun currentCommunicationDevice(): AudioRouteController.CommunicationDevice? =
        if (sdkInt >= Build.VERSION_CODES.S) audioManager.communicationDevice?.let {
            AudioRouteController.CommunicationDevice(it.type, it.productName?.toString().orEmpty())
        } else null

    override fun setCommunicationDevice(device: AudioRouteController.CommunicationDevice): Boolean =
        if (sdkInt >= Build.VERSION_CODES.S) audioManager.availableCommunicationDevices
            .firstOrNull { it.type == device.type && it.productName?.toString() == device.name }
            ?.let { audioManager.setCommunicationDevice(it) } ?: false
        else false

    override fun clearCommunicationDevice() {
        if (sdkInt >= Build.VERSION_CODES.S) audioManager.clearCommunicationDevice()
    }

    override fun isSpeakerphoneOn(): Boolean = audioManager.isSpeakerphoneOn
    override fun setSpeakerphoneOn(enabled: Boolean) { audioManager.isSpeakerphoneOn = enabled }
    override fun startBluetoothSco(): Boolean = runCatching {
        audioManager.startBluetoothSco()
        true
    }.getOrDefault(false)
    override fun stopBluetoothSco() { audioManager.stopBluetoothSco() }
    override fun describePlaybackRoute(): String {
        if (sdkInt >= Build.VERSION_CODES.S) audioManager.communicationDevice?.let {
            return "${it.type}:${it.productName}"
        }
        return when {
            audioManager.isBluetoothScoOn -> "BLUETOOTH_SCO"
            audioManager.isSpeakerphoneOn -> "BUILTIN_SPEAKER"
            else -> "PLATFORM_DEFAULT"
        }
    }

    override fun describeInputDeviceType(): String = audioManager
        .getDevices(AudioManager.GET_DEVICES_INPUTS)
        .firstOrNull()
        ?.let { audioDeviceTypeName(it.type) }
        ?: "UNKNOWN"

    override fun describeOutputDeviceType(): String = audioManager
        .getDevices(AudioManager.GET_DEVICES_OUTPUTS)
        .firstOrNull()
        ?.let { audioDeviceTypeName(it.type) }
        ?: "UNKNOWN"
}

private fun audioDeviceTypeName(type: Int): String = when (type) {
    AudioDeviceInfo.TYPE_BUILTIN_MIC -> "BUILTIN_MIC"
    AudioDeviceInfo.TYPE_BUILTIN_SPEAKER -> "BUILTIN_SPEAKER"
    AudioDeviceInfo.TYPE_BUILTIN_EARPIECE -> "BUILTIN_EARPIECE"
    AudioDeviceInfo.TYPE_WIRED_HEADSET -> "WIRED_HEADSET"
    AudioDeviceInfo.TYPE_WIRED_HEADPHONES -> "WIRED_HEADPHONES"
    AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "BLUETOOTH_SCO"
    AudioDeviceInfo.TYPE_BLE_HEADSET -> "BLE_HEADSET"
    AudioDeviceInfo.TYPE_BLE_SPEAKER -> "BLE_SPEAKER"
    else -> "TYPE_$type"
}
