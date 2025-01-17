// Copyright 2001-2015 Crytek GmbH. All rights reserved.

#include "StdAfx.h"
#include "ComputeRenderPass.h"
#include "DriverD3D.h"

bool CComputeRenderPass::OnResourceInvalidated(void* pThis, uint32 flags) 
{
	reinterpret_cast<CComputeRenderPass*>(pThis)->m_dirtyMask |= eDirty_Resources;
	// Don't keep the callback when the resource goes out of scope
	return !(flags & eResourceDestroyed);
}

CComputeRenderPass::CComputeRenderPass(EPassFlags flags)
	: m_flags(flags)
	, m_dirtyMask(eDirty_All)
	, m_bResourcesInvalidated(false)
	, m_pShader(nullptr)
	, m_rtMask(0)
	, m_dispatchSizeX(1)
	, m_dispatchSizeY(1)
	, m_dispatchSizeZ(1)
	, m_currentPsoUpdateCount(0)
	, m_bPendingConstantUpdate(false)
	, m_bCompiled(false)
	, m_resourceDesc(this, OnResourceInvalidated)
{
	m_inputVars[0] = m_inputVars[1] = m_inputVars[2] = m_inputVars[3] = 0;
	m_pResourceSet = GetDeviceObjectFactory().CreateResourceSet(CDeviceResourceSet::EFlags_ForceSetAllState);

	SetLabel("COMPUTE_PASS");
}

uint CComputeRenderPass::Compile()
{
	CD3D9Renderer* const __restrict rd = gcpRendD3D;

	uint32 dirtyMask = m_dirtyMask;
	dirtyMask |= m_bResourcesInvalidated ? eDirty_Resources : eDirty_None;

	m_bResourcesInvalidated = false;
	m_bCompiled = false;

	if (dirtyMask & eDirty_Resources)
	{
		if (!m_pResourceSet->Update(m_resourceDesc, CDeviceResourceSetDesc::EDirtyFlags(dirtyMask & (eDirty_Resources | eDirty_ResourceLayout))))
			return dirtyMask;
	}

	if (dirtyMask & (eDirty_Technique | eDirty_ResourceLayout))
	{
		m_constantManager.ReleaseShaderReflection();

		if (m_flags & eFlags_ReflectConstantBuffersFromShader)
		{
			m_constantManager.AllocateShaderReflection(m_pShader, m_techniqueName, m_rtMask, EShaderStage_Compute);
		}

		// Resource layout
		int bindSlot = 0;
		SDeviceResourceLayoutDesc resourceLayoutDesc;

		resourceLayoutDesc.SetResourceSet(bindSlot++, m_resourceDesc);
		for (auto& cb : m_constantManager.GetBuffers())
			resourceLayoutDesc.SetConstantBuffer(bindSlot++, cb.shaderSlot, cb.shaderStages);

		m_pResourceLayout = GetDeviceObjectFactory().CreateResourceLayout(resourceLayoutDesc);

		if (!m_pResourceLayout)
			return dirtyMask;
	}

	if (dirtyMask & (eDirty_Technique | eDirty_ResourceLayout))
	{
		// Pipeline state
		CDeviceComputePSODesc psoDesc(m_pResourceLayout, m_pShader, m_techniqueName, m_rtMask, 0, 0);
		m_pPipelineState = GetDeviceObjectFactory().CreateComputePSO(psoDesc);

		if (!m_pPipelineState || !m_pPipelineState->IsValid())
			return dirtyMask;

		m_currentPsoUpdateCount = m_pPipelineState->GetUpdateCount();

		if (m_flags & eFlags_ReflectConstantBuffersFromShader)
			m_constantManager.InitShaderReflection(*m_pPipelineState);
	}

	dirtyMask = eDirty_None;
	m_bCompiled = true;
	return dirtyMask;
}

void CComputeRenderPass::BeginConstantUpdate()
{
	if (m_flags & eFlags_ReflectConstantBuffersFromShader)
	{
		if (IsDirty())
		{
			m_dirtyMask = Compile();
		}

		m_bPendingConstantUpdate = true;
		m_constantManager.BeginNamedConstantUpdate();
	}
}

void CComputeRenderPass::PrepareResourcesForUse(CDeviceCommandListRef RESTRICT_REFERENCE commandList)
{
	CD3D9Renderer* const __restrict rd = gcpRendD3D;

	if (m_bPendingConstantUpdate)
	{
		if (m_bCompiled)
		{
			CRY_ASSERT(!IsDirty()); // compute pass modified AFTER call to BeginConstantUpdate
		}

		// Unmap constant buffers and mark as bound
		m_constantManager.EndNamedConstantUpdate();
		m_bPendingConstantUpdate = false;
	}
	else if (IsDirty())
	{
		m_dirtyMask = Compile();
	}

	if (m_dirtyMask == eDirty_None)
	{
		CDeviceComputeCommandInterface* pComputeInterface = commandList.GetComputeInterface();
		auto& inlineConstantBuffers = m_constantManager.GetBuffers();

		// Prepare resources
		int bindSlot = 0;
		pComputeInterface->PrepareResourcesForUse(bindSlot++, m_pResourceSet.get());

		for (auto& cb : inlineConstantBuffers)
			pComputeInterface->PrepareInlineConstantBufferForUse(bindSlot++, cb.pBuffer, cb.shaderSlot, EShaderStage_Compute);
	}
}

void CComputeRenderPass::BeginRenderPass(CDeviceCommandListRef RESTRICT_REFERENCE commandList)
{
	// Note: Function has to be threadsafe since it can be called from several worker threads

#if defined(ENABLE_PROFILING_CODE)
	commandList.BeginProfilingSection();
#endif
}

void CComputeRenderPass::EndRenderPass(CDeviceCommandListRef RESTRICT_REFERENCE commandList)
{
	// Note: Function has to be threadsafe since it can be called from several worker threads

#if defined(ENABLE_PROFILING_CODE)
	m_profilingStats.Merge(commandList.EndProfilingSection());
#endif

	// Nothing to cleanup at the moment
}

void CComputeRenderPass::Dispatch(CDeviceCommandListRef RESTRICT_REFERENCE commandList, ::EShaderStage srvUsage)
{
	if (m_dirtyMask == eDirty_None)
	{
		CDeviceComputeCommandInterface* pComputeInterface = commandList.GetComputeInterface();
		auto& inlineConstantBuffers = m_constantManager.GetBuffers();

		// Draw to command list
		int bindSlot = 0;
		pComputeInterface->SetResourceLayout(m_pResourceLayout.get());
		pComputeInterface->SetPipelineState(m_pPipelineState.get());
		pComputeInterface->SetResources(bindSlot++, m_pResourceSet.get());

		for (auto& cb : inlineConstantBuffers)
			pComputeInterface->SetInlineConstantBuffer(bindSlot++, cb.pBuffer, cb.shaderSlot);

		pComputeInterface->Dispatch(m_dispatchSizeX, m_dispatchSizeY, m_dispatchSizeZ);
	}
}

void CComputeRenderPass::Execute(CDeviceCommandListRef RESTRICT_REFERENCE commandList, ::EShaderStage srvUsage)
{
	if (gcpRendD3D->GetGraphicsPipeline().GetRenderPassScheduler().IsActive())
	{
		gcpRendD3D->GetGraphicsPipeline().GetRenderPassScheduler().AddPass(this);
		return;
	}
	
	BeginRenderPass(commandList);
	Dispatch(commandList, srvUsage);
	EndRenderPass(commandList);
}

void CComputeRenderPass::Reset()
{
	m_flags = eFlags_None;
	m_dirtyMask = eDirty_All;

	ZeroArray(m_inputVars);
	m_bResourcesInvalidated = true;
	m_bPendingConstantUpdate = true;
	m_bCompiled = false;

	m_pShader = nullptr;
	m_techniqueName.reset();
	m_rtMask = 0;

	m_dispatchSizeX = 0;
	m_dispatchSizeY = 0;
	m_dispatchSizeZ = 0;

	m_resourceDesc.Clear();
	m_pResourceSet.reset();
	m_pResourceLayout.reset();
	m_pPipelineState.reset();
	m_currentPsoUpdateCount = 0;

	m_constantManager.Reset();

	m_profilingStats.Reset();
}

