import * as React from "react";
import styled from "styled-components";
import gql from "graphql-tag";
import { NonIdealState, Colors, Icon } from "@blueprintjs/core";
import { IconNames } from "@blueprintjs/icons";
import ExecutionPlan from "../ExecutionPlan";
import {
  RunPreviewExecutionPlanResultFragment,
  RunPreviewExecutionPlanResultFragment_PipelineConfigEvaluationError_errors
} from "./types/RunPreviewExecutionPlanResultFragment";

interface IRunPreviewProps {
  plan?: RunPreviewExecutionPlanResultFragment;
}

export class RunPreview extends React.Component<IRunPreviewProps> {
  static fragments = {
    RunPreviewExecutionPlanResultFragment: gql`
      fragment RunPreviewExecutionPlanResultFragment on ExecutionPlanResult {
        __typename
        ... on ExecutionPlan {
          ...ExecutionPlanFragment
        }
        ... on PipelineNotFoundError {
          message
        }
        ... on PipelineConfigValidationInvalid {
          errors {
            reason
            message
          }
        }
        ... on PipelineConfigEvaluationError {
          errors {
            reason
            message
          }
        }
      }
      ${ExecutionPlan.fragments.ExecutionPlanFragment}
    `
  };

  render() {
    const { plan } = this.props;

    let errors: RunPreviewExecutionPlanResultFragment_PipelineConfigEvaluationError_errors[] = [];
    if (plan && plan.__typename === "PipelineConfigValidationInvalid") {
      errors = plan.errors;
    }
    if (plan && plan.__typename === "PipelineConfigEvaluationError") {
      errors = plan.errors;
    }

    return plan && plan.__typename === "ExecutionPlan" ? (
      <ExecutionPlan executionPlan={plan} />
    ) : (
      <NonIdealWrap>
        <NonIdealState
          icon={IconNames.SEND_TO_GRAPH}
          title="No Execution Plan"
          description={
            errors.length
              ? `Fix the ${errors.length.toLocaleString()} ${
                  errors.length == 1 ? "error" : "errors"
                } below to preview the execution plan.`
              : `Provide valid configuration to see an execution plan.`
          }
        />
        <ErrorsWrap>
          {errors.map((e, idx) => (
            <ErrorRow key={idx}>
              <div style={{ paddingRight: 8 }}>
                <Icon icon="error" iconSize={14} color={Colors.RED4} />
              </div>
              {e.message}
            </ErrorRow>
          ))}
        </ErrorsWrap>
      </NonIdealWrap>
    );
  }
}

const NonIdealWrap = styled.div`
  display: flex;
  flex-direction: column;
  padding: 20px;
  padding-top: 12vh;
  overflow: scroll;
`;
const ErrorsWrap = styled.div`
  padding-top: 2vh;
  font-size: 0.9em;
  color: ${Colors.BLACK};
`;

const ErrorRow = styled.div`
  text-align: left;
  white-space: pre-wrap;
  word-break: break-word;
  display: flex;
  flex-direction: row;
  align-items: flex-start;
  background: #eee;
  border-radius: 4px;
  padding: 10px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
  margin-bottom: 8px;
`;
