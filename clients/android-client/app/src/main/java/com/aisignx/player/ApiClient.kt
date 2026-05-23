package com.aisignx.player

import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.util.concurrent.TimeUnit

object ApiClient {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .hostnameVerifier { _, _ -> true }   // allow self-signed certs on LAN servers
        .build()

    private val JSON = "application/json; charset=utf-8".toMediaType()

    /** POST /api/register */
    fun register(
        serverUrl: String,
        deviceId: String,
        friendlyName: String,
        hostname: String,
        os: String,
        resolution: String,
        appVersion: String,
        enrollmentCode: String
    ): RegisterResult {
        val body = JSONObject().apply {
            put("device_id", deviceId)
            put("friendly_name", friendlyName)
            put("hostname", hostname)
            put("os", os)
            put("resolution", resolution)
            put("app_version", appVersion)
            // Server requires a per-tenant enrollment code so a hostile
            // device cannot pick which tenant it lands in. Without one
            // /api/register returns 400.
            put("enrollment_code", enrollmentCode)
        }
        return try {
            val req = Request.Builder()
                .url("$serverUrl/api/register")
                .post(body.toString().toRequestBody(JSON))
                .build()
            client.newCall(req).execute().use { resp ->
                val json = JSONObject(resp.body?.string() ?: "{}")
                when (json.optString("status")) {
                    "approved" -> RegisterResult.Approved(json.optString("token"))
                    "pending"  -> RegisterResult.Pending
                    else       -> RegisterResult.Error(json.optString("message", "Unknown error"))
                }
            }
        } catch (e: Exception) {
            RegisterResult.Error(e.message ?: "Network error")
        }
    }

    /** GET /api/register/status/<device_id> */
    fun pollStatus(serverUrl: String, deviceId: String): PollResult {
        return try {
            val req = Request.Builder().url("$serverUrl/api/register/status/$deviceId").get().build()
            client.newCall(req).execute().use { resp ->
                val json = JSONObject(resp.body?.string() ?: "{}")
                when (json.optString("status")) {
                    "approved" -> PollResult.Approved(json.optString("token"))
                    "declined" -> PollResult.Declined
                    else       -> PollResult.Pending
                }
            }
        } catch (e: Exception) {
            PollResult.NetworkError
        }
    }

    /** Quick reachability check â€” just hits /api/register with empty body and looks for a JSON response */
    fun ping(serverUrl: String): Boolean {
        return try {
            val req = Request.Builder()
                .url("$serverUrl/api/register")
                .post("{}".toRequestBody(JSON))
                .build()
            client.newCall(req).execute().use { resp ->
                resp.isSuccessful || resp.code in 400..499  // 4xx = server reached, request invalid
            }
        } catch (_: Exception) { false }
    }

    /** Returns true when remoteVer is strictly newer than localVer (semver x.y.z). */
    fun isRemoteVersionNewer(local: String, remote: String): Boolean {
        val l = local.split(".").map  { it.toIntOrNull() ?: 0 }
        val r = remote.split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0..2) {
            val li = l.getOrElse(i) { 0 }
            val ri = r.getOrElse(i) { 0 }
            if (ri > li) return true
            if (ri < li) return false
        }
        return false
    }

    /** GET /api/version — returns null if no update available or on network error */
    fun checkForUpdate(serverUrl: String, currentVersion: String): UpdateInfo? {
        return try {
            val req = Request.Builder().url("$serverUrl/api/version").get().build()
            client.newCall(req).execute().use { resp ->
                val json = JSONObject(resp.body?.string() ?: "{}")
                val android = json.optJSONObject("clients")?.optJSONObject("android") ?: return null
                val remoteVer = android.optString("version").takeIf { it.isNotEmpty() } ?: return null
                if (isRemoteVersionNewer(currentVersion, remoteVer)) {
                    UpdateInfo(
                        version     = remoteVer,
                        downloadUrl = serverUrl + android.optString("url")
                    )
                } else null
            }
        } catch (_: Exception) { null }
    }

    data class UpdateInfo(val version: String, val downloadUrl: String)

    sealed class RegisterResult {
        data class Approved(val token: String) : RegisterResult()
        object Pending : RegisterResult()
        data class Error(val message: String) : RegisterResult()
    }

    sealed class PollResult {
        data class Approved(val token: String) : PollResult()
        object Declined : PollResult()
        object Pending : PollResult()
        object NetworkError : PollResult()
    }
}
